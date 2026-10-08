import logging
import time

log = logging.getLogger("sentinel.scheduler")

CHANNEL_OF = {
    "alert": "discord",
    "finish": "discord",
    "notice": "discord",
    "analyze": "gemini",
}

MAX_BACKOFF_SECONDS = 600


class Pacer:
    """Spaces out calls to one service.

    A job due at `due` runs at max(due, previous run + gap), so work that arrives
    faster than the gap queues up in order instead of being dropped.
    """

    def __init__(self, outbox, channel, gap):
        self.outbox = outbox
        self.channel = channel
        self.gap = gap

    def ready_at(self, due):
        last = self.outbox.get_pace(self.channel)
        return due if last is None else max(due, last + self.gap)

    def record(self, at):
        self.outbox.set_pace(self.channel, at)

    def push_back(self, until):
        last = self.outbox.get_pace(self.channel) or 0
        self.outbox.set_pace(self.channel, max(last, until - self.gap))


class Dispatcher:
    """Runs queued jobs for the given channels, one at a time, in due order."""

    def __init__(
        self,
        outbox,
        channels,
        handlers,
        pacers,
        clock=time.time,
        max_retries=5,
        backoff=10,
        policy="defer",
        on_give_up=None,
        on_drop=None,
        idle=0.5,
    ):
        self.outbox = outbox
        self.channels = set(channels)
        self.handlers = handlers
        self.pacers = pacers
        self.clock = clock
        self.max_retries = max_retries
        self.backoff = backoff
        self.policy = policy
        self.on_give_up = on_give_up or {}
        self.on_drop = on_drop or {}
        self.idle = idle

    def _next(self):
        best = None
        seen = set()
        for job in self.outbox.runnable_jobs():
            channel = CHANNEL_OF[job["kind"]]
            # The first job in a channel blocks the rest, which keeps each channel in order.
            if channel not in self.channels or channel in seen:
                continue
            seen.add(channel)
            run_at = self.pacers[channel].ready_at(job["due"])
            if best is None or run_at < best[0]:
                best = (run_at, job)
        return best

    def step(self):
        """Run at most one job that is ready now. Returns True if it did anything."""
        pick = self._next()
        if pick is None:
            return False
        run_at, job = pick
        now = self.clock()
        if run_at > now:
            return False
        if self.policy == "drop" and run_at > job["due"] and job["kind"] in self.on_drop:
            self._drop(job)
            return True
        self._execute(job, now)
        return True

    def _drop(self, job):
        self.outbox.update_job(job["id"], state="dropped")
        event = self.outbox.get_event(job["event_id"]) if job["event_id"] else None
        self.on_drop[job["kind"]](job, event)

    def _execute(self, job, now):
        pacer = self.pacers[CHANNEL_OF[job["kind"]]]
        event = self.outbox.get_event(job["event_id"]) if job["event_id"] else None
        pacer.record(now)
        try:
            self.handlers[job["kind"]](job, event)
        except Exception as exc:
            self._failed(job, event, exc, now, pacer)
        else:
            self.outbox.update_job(job["id"], state="done")

    def _failed(self, job, event, exc, now, pacer):
        attempts = job["attempts"] + 1
        error = f"{type(exc).__name__}: {exc}"[:300]
        if attempts >= self.max_retries:
            log.error("Giving up on %s job %s after %d tries: %s", job["kind"], job["id"], attempts, error)
            self.outbox.update_job(job["id"], state="failed", attempts=attempts, last_error=error)
            hook = self.on_give_up.get(job["kind"])
            if hook:
                try:
                    hook(job, event, exc)
                except Exception:
                    log.exception("Give-up hook failed for job %s", job["id"])
            return

        retry_after = getattr(exc, "retry_after", None)
        if retry_after is not None:
            delay = retry_after
            pacer.push_back(now + delay)
        else:
            delay = min(MAX_BACKOFF_SECONDS, self.backoff * 2 ** (attempts - 1))
        log.warning("%s job %s failed (%s), retry %d in %.0f s", job["kind"], job["id"], error, attempts, delay)
        self.outbox.update_job(job["id"], attempts=attempts, due=now + delay, last_error=error)

    def run(self, stop):
        while not stop.is_set():
            try:
                ran = self.step()
            except Exception:
                log.exception("Dispatcher step failed")
                ran = False
            if not ran:
                stop.wait(self.idle)
