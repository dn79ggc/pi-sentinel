from types import SimpleNamespace

import pytest

from notifier import DiscordError, RateLimited
from outbox import Outbox
from reporter import GeminiBusy, Reporter

NOW = 1_700_000_000.0


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


def settings(**overrides):
    values = dict(
        DISCORD_WEBHOOK_URL="https://discord.example/hook",
        GEMINI_MODEL="test-model",
        DAILY_CALL_CAP=50,
        QUOTA_TIMEZONE="America/Los_Angeles",
        MAX_ANALYSIS_AGE_MINUTES=120,
        MAX_PENDING_ANALYSES=50,
        BACKLOG_NOTICE_AT=5,
        GEMINI_COOLDOWN_SECONDS=30,
        RETRY_BACKOFF_SECONDS=10,
        CLIP_MAX_MB=8,
        DISCORD_TIMESTAMPS=False,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


class Harness:
    def __init__(self, tmp_path, **overrides):
        self.outbox = Outbox(":memory:")
        self.clock = Clock()
        self.posts = []
        self.edits = []
        self.describe_calls = []
        self.edit_error = None
        self.describe_error = None
        self.reporter = Reporter(
            self.outbox, settings(**overrides), self._describe, self._post, self._edit, clock=self.clock
        )
        self.tmp = tmp_path

    def _describe(self, jpegs, model, reason, seconds):
        self.describe_calls.append((list(jpegs), model, reason, seconds))
        if self.describe_error:
            raise self.describe_error
        return "A person walks in."

    def _post(self, url, text, attachments=()):
        self.posts.append((text, list(attachments)))
        return f"msg-{len(self.posts)}"

    def _edit(self, url, message_id, text, attachments=()):
        if self.edit_error:
            raise self.edit_error
        self.edits.append((message_id, text, list(attachments)))

    def event(self, detected_ago=10, frames=2, clip_bytes=None, message_id="msg-0"):
        event_id = self.outbox.create_event(self.clock.now - detected_ago, "motion")
        keyframes = []
        for i in range(frames):
            path = self.tmp / f"kf-{event_id}-{i}.jpg"
            path.write_bytes(b"jpeg%d" % i)
            keyframes.append(path)
        clip = None
        if clip_bytes is not None:
            clip = self.tmp / f"clip-{event_id}.mp4"
            clip.write_bytes(clip_bytes)
        self.outbox.set_clip(event_id, clip, keyframes, 14.2)
        self.outbox.close_event(event_id, self.clock.now)
        if message_id:
            self.outbox.set_message_id(event_id, message_id)
        return event_id

    def job(self, kind, event_id=None, **fields):
        job_id = self.outbox.add_job(kind, self.clock.now, event_id=event_id)
        job = next(j for j in self.outbox.runnable_jobs() if j["id"] == job_id) if kind not in ("analyze", "finish") else {
            "id": job_id, "kind": kind, "event_id": event_id, "attempts": 0, "payload": None, "due": self.clock.now,
        }
        job.update(fields)
        return job

    def event_row(self, event_id):
        return self.outbox.get_event(event_id)

    def finish_jobs(self):
        return [j for j in self.outbox.runnable_jobs() if j["kind"] == "finish"]


@pytest.fixture
def h(tmp_path):
    harness = Harness(tmp_path)
    yield harness
    harness.outbox.close()


def test_send_alert_posts_and_remembers_the_message(h):
    event_id = h.outbox.create_event(NOW - 1, "motion")
    h.reporter.send_alert(h.job("alert", event_id), h.event_row(event_id))
    assert h.posts[0][0].startswith(f"Event {event_id} | detected ")
    assert h.event_row(event_id)["message_id"] == "msg-1"


def test_analyze_calls_gemini_with_saved_frames_and_queues_the_report(h):
    event_id = h.event()
    h.reporter.analyze(h.job("analyze", event_id), h.event_row(event_id))
    jpegs, model, reason, seconds = h.describe_calls[0]
    assert jpegs == [b"jpeg0", b"jpeg1"]
    assert (model, reason, seconds) == ("test-model", "motion", 14.2)
    assert h.event_row(event_id)["analysis"] == "A person walks in."
    assert len(h.finish_jobs()) == 1


def test_analyze_counts_the_call_toward_the_daily_cap(h):
    event_id = h.event()
    h.reporter.analyze(h.job("analyze", event_id), h.event_row(event_id))
    day = h.reporter._day(h.clock.now)
    assert h.outbox.calls_on(day) == 1


def test_analyze_skips_when_the_daily_cap_is_reached(tmp_path):
    h = Harness(tmp_path, DAILY_CALL_CAP=1)
    h.outbox.add_call(h.reporter._day(h.clock.now))
    event_id = h.event()
    h.reporter.analyze(h.job("analyze", event_id), h.event_row(event_id))
    assert h.describe_calls == []
    assert "daily cap of 1" in h.event_row(event_id)["note"]
    assert len(h.finish_jobs()) == 1


def test_analyze_skips_events_that_are_too_old(h):
    event_id = h.event(detected_ago=3 * 3600)
    h.reporter.analyze(h.job("analyze", event_id), h.event_row(event_id))
    assert h.describe_calls == []
    assert "120 minutes" in h.event_row(event_id)["note"]


def test_analyze_skips_when_frames_are_gone(h):
    event_id = h.event(frames=0)
    h.reporter.analyze(h.job("analyze", event_id), h.event_row(event_id))
    assert h.describe_calls == []
    assert "no frames" in h.event_row(event_id)["note"]


def test_quota_error_becomes_a_channel_wide_delay_that_grows(h):
    h.describe_error = RuntimeError("429 RESOURCE_EXHAUSTED")
    event_id = h.event()
    with pytest.raises(GeminiBusy) as first:
        h.reporter.analyze(h.job("analyze", event_id, attempts=0), h.event_row(event_id))
    with pytest.raises(GeminiBusy) as third:
        h.reporter.analyze(h.job("analyze", event_id, attempts=2), h.event_row(event_id))
    assert (first.value.retry_after, third.value.retry_after) == (10, 40)
    assert h.finish_jobs() == []


def test_other_gemini_errors_propagate_for_the_normal_retry(h):
    h.describe_error = ValueError("bad request")
    event_id = h.event()
    with pytest.raises(ValueError):
        h.reporter.analyze(h.job("analyze", event_id), h.event_row(event_id))


def test_finish_edits_the_alert_with_the_clip(h):
    event_id = h.event(clip_bytes=b"mp4data")
    h.outbox.set_analysis(event_id, "A person walks in.")
    h.reporter.finish(h.job("finish", event_id), h.event_row(event_id))
    message_id, text, attachments = h.edits[0]
    assert message_id == "msg-0"
    assert text.startswith(f"Event {event_id} | detected ")
    assert "14 s long" in text
    assert text.endswith("A person walks in.")
    assert attachments == [(f"event-{event_id}.mp4", b"mp4data", "video/mp4")]
    assert h.event_row(event_id)["final_posted_at"] == NOW


def test_finish_posts_a_new_message_when_the_edit_is_rejected(h):
    h.edit_error = DiscordError("Unknown Message")
    event_id = h.event(clip_bytes=b"mp4data")
    h.outbox.set_analysis(event_id, "A person walks in.")
    h.reporter.finish(h.job("finish", event_id), h.event_row(event_id))
    assert len(h.posts) == 1
    assert h.event_row(event_id)["message_id"] == "msg-1"


def test_finish_lets_rate_limits_through_for_a_retry(h):
    h.edit_error = RateLimited(3)
    event_id = h.event(clip_bytes=b"x")
    h.outbox.set_analysis(event_id, "text")
    with pytest.raises(RateLimited):
        h.reporter.finish(h.job("finish", event_id), h.event_row(event_id))
    assert h.posts == []
    assert h.event_row(event_id)["final_posted_at"] is None


def test_finish_posts_instead_of_editing_when_there_is_no_alert_message(h):
    event_id = h.event(message_id=None)
    h.outbox.set_analysis(event_id, "text")
    h.reporter.finish(h.job("finish", event_id), h.event_row(event_id))
    assert h.edits == []
    assert len(h.posts) == 1


def test_oversized_clip_is_not_uploaded(tmp_path):
    h = Harness(tmp_path, CLIP_MAX_MB=0.000001)
    event_id = h.event(clip_bytes=b"x" * 100)
    h.outbox.set_analysis(event_id, "text")
    h.reporter.finish(h.job("finish", event_id), h.event_row(event_id))
    _, text, attachments = h.edits[0]
    assert attachments == []
    assert "stays on the Pi" in text


def test_missing_clip_falls_back_to_key_frames(h):
    event_id = h.event(clip_bytes=None, frames=3)
    h.outbox.set_analysis(event_id, "text")
    h.reporter.finish(h.job("finish", event_id), h.event_row(event_id))
    _, text, attachments = h.edits[0]
    assert [name for name, _, _ in attachments] == [f"event-{event_id}-{i}.jpg" for i in (1, 2, 3)]
    assert "key frames" in text


def test_finish_shows_the_note_when_there_is_no_analysis(h):
    event_id = h.event(clip_bytes=b"x")
    h.outbox.set_note(event_id, "Analysis skipped: the daily cap of 50 Gemini calls was reached.")
    h.reporter.finish(h.job("finish", event_id), h.event_row(event_id))
    assert "Analysis skipped: the daily cap" in h.edits[0][1]


def test_queue_analysis_adds_a_job(h):
    event_id = h.event()
    h.reporter.queue_analysis(event_id)
    assert h.outbox.count_pending("analyze") == 1


def test_queue_analysis_skips_when_the_queue_is_full(tmp_path):
    h = Harness(tmp_path, MAX_PENDING_ANALYSES=2)
    for _ in range(2):
        h.reporter.queue_analysis(h.event())
    last = h.event()
    h.reporter.queue_analysis(last)
    assert h.outbox.count_pending("analyze") == 2
    assert "2 analyses were already waiting" in h.event_row(last)["note"]
    assert len(h.finish_jobs()) == 1


def test_backlog_notice_is_sent_once_and_rearms_after_the_queue_drains(h):
    for _ in range(7):
        h.reporter.queue_analysis(h.event())
    notices = [j for j in h.outbox.runnable_jobs() if j["kind"] == "notice"]
    assert len(notices) == 1
    assert notices[0]["payload"] == "Backlog: 5 analyses waiting, about 2.5 min behind."
    for job in [j for j in h.outbox.runnable_jobs() if j["kind"] == "analyze"][:5]:
        h.outbox.update_job(job["id"], state="done")
    h.reporter._backlog_check(h.outbox.count_pending("analyze"), h.clock.now)
    assert h.outbox.get_meta("backlog_notified") == "0"


def test_give_up_hook_queues_a_report_that_says_what_failed(h):
    event_id = h.event()
    h.reporter.analysis_failed({"attempts": 4}, h.event_row(event_id), RuntimeError("boom"))
    assert h.event_row(event_id)["note"] == "Analysis failed after 5 tries (RuntimeError)."
    assert len(h.finish_jobs()) == 1


def test_drop_hook_queues_a_report(h):
    event_id = h.event()
    h.reporter.analysis_dropped({}, h.event_row(event_id))
    assert "cooldown" in h.event_row(event_id)["note"]
    assert len(h.finish_jobs()) == 1
