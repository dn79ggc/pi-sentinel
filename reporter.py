import logging
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from llm import is_quota_error
from notifier import RateLimited, DiscordError, alert_text, backlog_text, report_text

log = logging.getLogger("sentinel.reporter")

MAX_ATTACHMENTS = 10  # Discord's limit per message.


class GeminiBusy(RuntimeError):
    """Gemini answered 429. retry_after pushes the whole Gemini channel back."""

    def __init__(self, retry_after):
        super().__init__("Gemini is rate limited")
        self.retry_after = retry_after


class Reporter:
    """The work behind each job kind: post the alert, call Gemini, edit the alert into the report."""

    def __init__(self, outbox, settings, describe, post, edit, clock=time.time):
        self.outbox = outbox
        self.s = settings
        self.describe = describe
        self.post = post
        self.edit = edit
        self.clock = clock

    def handlers(self):
        return {
            "alert": self.send_alert,
            "analyze": self.analyze,
            "finish": self.finish,
            "notice": self.notice,
        }

    def _day(self, now):
        return datetime.fromtimestamp(now, ZoneInfo(self.s.QUOTA_TIMEZONE)).date().isoformat()

    def _timestamps(self):
        return self.s.DISCORD_TIMESTAMPS

    def queue_analysis(self, event_id):
        now = self.clock()
        waiting = self.outbox.count_pending("analyze")
        if waiting >= self.s.MAX_PENDING_ANALYSES:
            self.outbox.set_note(event_id, f"Analysis skipped: {waiting} analyses were already waiting.")
            self.outbox.add_job("finish", now, event_id=event_id)
            return
        self.outbox.add_job("analyze", now, event_id=event_id)
        self._backlog_check(waiting + 1, now)

    def _backlog_check(self, waiting, now):
        notified = self.outbox.get_meta("backlog_notified") == "1"
        if waiting >= self.s.BACKLOG_NOTICE_AT and not notified:
            self.outbox.set_meta("backlog_notified", "1")
            minutes = waiting * self.s.GEMINI_COOLDOWN_SECONDS / 60
            self.outbox.add_job("notice", now, payload=backlog_text(waiting, minutes))
        elif waiting < self.s.BACKLOG_NOTICE_AT and notified:
            self.outbox.set_meta("backlog_notified", "0")

    def send_alert(self, job, event):
        text = alert_text(event["id"], event["detected_at"], event["reason"], self._timestamps())
        message_id = self.post(self.s.DISCORD_WEBHOOK_URL, text)
        self.outbox.set_message_id(event["id"], message_id)

    def notice(self, job, event):
        self.post(self.s.DISCORD_WEBHOOK_URL, job["payload"])

    def analyze(self, job, event):
        now = self.clock()
        event_id = event["id"]
        skip = self._skip_reason(event, now)
        if skip:
            self.outbox.set_note(event_id, f"Analysis skipped: {skip}")
        else:
            self._call_gemini(job, event, now)
        self.outbox.add_job("finish", now, event_id=event_id)
        self._backlog_check(self.outbox.count_pending("analyze") - 1, now)

    def _skip_reason(self, event, now):
        if now - event["detected_at"] > self.s.MAX_ANALYSIS_AGE_MINUTES * 60:
            return f"this event is more than {self.s.MAX_ANALYSIS_AGE_MINUTES:g} minutes old."
        if self.outbox.calls_on(self._day(now)) >= self.s.DAILY_CALL_CAP:
            return f"the daily cap of {self.s.DAILY_CALL_CAP} Gemini calls was reached."
        if not [p for p in event["keyframes"] if Path(p).exists()]:
            return "no frames were saved for this event."
        return None

    def _call_gemini(self, job, event, now):
        jpegs = [Path(p).read_bytes() for p in event["keyframes"] if Path(p).exists()]
        self.outbox.add_call(self._day(now))
        try:
            text = self.describe(jpegs, self.s.GEMINI_MODEL, event["reason"], event["clip_seconds"])
        except Exception as exc:
            if is_quota_error(exc):
                delay = min(600, self.s.RETRY_BACKOFF_SECONDS * 2 ** job["attempts"])
                raise GeminiBusy(delay) from exc
            raise
        self.outbox.set_analysis(event["id"], text or "Gemini returned no text.")

    def finish(self, job, event):
        event_id = event["id"]
        body = event["analysis"] or event["note"] or "No analysis was produced."
        attachments, extra = self._attachments(event)
        if extra:
            body = f"{body}\n{extra}"
        posted_at = self.clock()
        text = report_text(
            event_id, event["detected_at"], posted_at, event["clip_seconds"], body, self._timestamps()
        )
        message_id = event["message_id"]
        if message_id:
            try:
                self.edit(self.s.DISCORD_WEBHOOK_URL, message_id, text, attachments)
            except RateLimited:
                raise
            except DiscordError as exc:
                log.warning("Editing event %s failed (%s), posting a new message", event_id, exc)
                message_id = None
        if not message_id:
            new_id = self.post(self.s.DISCORD_WEBHOOK_URL, text, attachments)
            self.outbox.set_message_id(event_id, new_id)
        self.outbox.set_final_posted(event_id, posted_at)

    def _attachments(self, event):
        limit = self.s.CLIP_MAX_MB * 1_000_000
        clip = Path(event["clip_path"]) if event["clip_path"] else None
        if clip and clip.exists():
            if clip.stat().st_size <= limit:
                return [(f"event-{event['id']}.mp4", clip.read_bytes(), "video/mp4")], None
            return [], f"The clip is over {self.s.CLIP_MAX_MB:g} MB, so it stays on the Pi: {clip}"
        frames = [Path(p) for p in event["keyframes"] if Path(p).exists()][:MAX_ATTACHMENTS]
        if frames:
            attachments = [(f"event-{event['id']}-{i + 1}.jpg", p.read_bytes(), "image/jpeg") for i, p in enumerate(frames)]
            return attachments, "No clip was made, so these are key frames."
        return [], None

    def analysis_failed(self, job, event, exc):
        kind = "rate limited" if isinstance(exc, GeminiBusy) else type(exc).__name__
        self.outbox.set_note(event["id"], f"Analysis failed after {job['attempts'] + 1} tries ({kind}).")
        self.outbox.add_job("finish", self.clock(), event_id=event["id"])

    def analysis_dropped(self, job, event):
        self.outbox.set_note(event["id"], "Analysis skipped: the Gemini cooldown had not passed.")
        self.outbox.add_job("finish", self.clock(), event_id=event["id"])
