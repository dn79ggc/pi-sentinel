import logging
from concurrent.futures import ThreadPoolExecutor

import clip
from events import Opened

log = logging.getLogger("sentinel.pipeline")


def process(frames, detector, tracker, buffer, sink, buffer_interval, encode):
    """Feed (timestamp, frame) pairs through the detector and event tracker.

    The detector sees every frame. Only one frame per buffer_interval is encoded
    into the buffer, which keeps memory and CPU low.
    """
    last_kept = None
    for timestamp, frame in frames:
        active = detector.update(frame)
        if last_kept is None or timestamp - last_kept >= buffer_interval:
            buffer.add(timestamp, encode(frame))
            last_kept = timestamp
        signal = tracker.update(timestamp, active)
        if signal is None:
            continue
        if isinstance(signal, Opened):
            sink.opened(signal)
        else:
            sink.closed(signal)
    closing = tracker.flush()
    if closing:
        sink.closed(closing)


class EventSink:
    """Turns tracker signals into database rows, jobs, and a clip built off the capture thread."""

    def __init__(self, outbox, buffer, reporter, settings, executor=None, make_clip=clip.encode_clip):
        self.outbox = outbox
        self.buffer = buffer
        self.reporter = reporter
        self.s = settings
        # One worker, so two ffmpeg runs never compete for the Pi's CPU.
        self.executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="clip")
        self.make_clip = make_clip
        self.current_id = None

    def opened(self, signal):
        self.current_id = self.outbox.create_event(signal.detected_at, signal.reason)
        self.outbox.add_job("alert", signal.detected_at, event_id=self.current_id)
        log.info("Event %d opened (%s)", self.current_id, signal.reason)

    def closed(self, signal):
        event_id = self.current_id
        self.current_id = None
        if event_id is None:
            return
        self.outbox.close_event(event_id, signal.last_motion_at)
        frames = self.buffer.window(
            signal.detected_at - self.s.PRE_ROLL_SECONDS,
            signal.last_motion_at + self.s.POST_ROLL_SECONDS,
        )
        log.info("Event %d closed with %d frames", event_id, len(frames))
        self.executor.submit(self._finalize, event_id, frames)

    def shutdown(self):
        self.executor.shutdown(wait=True)

    def _finalize(self, event_id, frames):
        try:
            self._build(event_id, frames)
        except Exception:
            log.exception("Building event %d failed", event_id)
            self.outbox.set_note(event_id, "No clip: processing this event failed.")
            self.outbox.add_job("finish", self.reporter.clock(), event_id=event_id)

    def _build(self, event_id, frames):
        if not frames:
            self.outbox.set_note(event_id, "No clip: no frames were buffered for this event.")
            self.outbox.add_job("finish", self.reporter.clock(), event_id=event_id)
            return

        folder = self.s.CLIP_DIR / f"event-{event_id:04d}"
        folder.mkdir(parents=True, exist_ok=True)
        keyframes = []
        for index, (_, jpeg) in enumerate(clip.pick_keyframes(frames, self.s.GEMINI_FRAMES), start=1):
            path = folder / f"key-{index:02d}.jpg"
            path.write_bytes(clip.shrink_jpeg(jpeg, self.s.SEND_WIDTH, self.s.JPEG_QUALITY))
            keyframes.append(path)

        clip_path = None
        if clip.ffmpeg_available():
            try:
                clip_path = self.make_clip(
                    frames, folder / "clip.mp4", self.s.CLIP_WIDTH, int(self.s.CLIP_MAX_MB * 1_000_000)
                )
            except clip.ClipError as exc:
                log.warning("Clip for event %d failed, sending key frames: %s", event_id, exc)
        else:
            log.warning("ffmpeg is not installed, so event %d has key frames only", event_id)

        seconds = frames[-1][0] - frames[0][0]
        self.outbox.set_clip(event_id, clip_path, keyframes, seconds)
        self.reporter.queue_analysis(event_id)
