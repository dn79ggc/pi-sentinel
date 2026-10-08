from types import SimpleNamespace

import numpy as np
import pytest

import clip
from buffer import FrameBuffer
from events import Closed, EventTracker, Opened
from motion import MotionDetector
from outbox import Outbox
from pipeline import EventSink, process
from reporter import Reporter
from scheduler import Dispatcher, Pacer

FRAME_GAP = 0.1
START = 1000.0


class Inline:
    """Stands in for the thread pool so tests run in order."""

    def submit(self, fn, *args):
        fn(*args)

    def shutdown(self, wait=True):
        pass


class Clock:
    def __init__(self, now=START):
        self.now = now

    def __call__(self):
        return self.now


def blank():
    return np.full((240, 320, 3), 80, dtype=np.uint8)


def moving():
    frame = blank()
    frame[60:160, 100:200] = 255
    return frame


def timed(frames):
    return [(START + i * FRAME_GAP, frame) for i, frame in enumerate(frames)]


def encode(frame):
    return clip.encode_jpeg(frame, width=320, quality=80)


class Recorder:
    def __init__(self):
        self.signals = []

    def opened(self, signal):
        self.signals.append(signal)

    def closed(self, signal):
        self.signals.append(signal)


def run(frames, sink, buffer=None):
    process(
        timed(frames),
        MotionDetector(warmup_frames=5),
        EventTracker(post_roll=2, max_event=30),
        buffer if buffer is not None else FrameBuffer(60),
        sink,
        buffer_interval=FRAME_GAP,
        encode=encode,
    )


BURST = [blank()] * 20 + [moving()] * 10 + [blank()] * 40


def test_one_burst_of_motion_is_one_event():
    sink = Recorder()
    buffer = FrameBuffer(60)
    run(BURST, sink, buffer)
    assert [type(s) for s in sink.signals] == [Opened, Closed]
    assert sink.signals[0].detected_at >= START + 2.0
    assert len(buffer) > 0


def test_static_footage_makes_no_events():
    sink = Recorder()
    run([blank()] * 40, sink)
    assert sink.signals == []


def test_event_still_open_when_frames_end_is_closed():
    sink = Recorder()
    run([blank()] * 20 + [moving()] * 5, sink)
    assert [type(s) for s in sink.signals] == [Opened, Closed]


def test_buffer_keeps_only_one_frame_per_interval():
    sink = Recorder()
    buffer = FrameBuffer(1000)
    frames = [(START + i * 0.01, blank()) for i in range(100)]
    process(
        frames, MotionDetector(warmup_frames=5), EventTracker(2, 30), buffer, sink,
        buffer_interval=0.1, encode=encode,
    )
    assert 9 <= len(buffer) <= 11


def make_settings(tmp_path):
    return SimpleNamespace(
        PRE_ROLL_SECONDS=1,
        POST_ROLL_SECONDS=2,
        CLIP_DIR=tmp_path / "clips",
        GEMINI_FRAMES=4,
        SEND_WIDTH=320,
        JPEG_QUALITY=80,
        CLIP_WIDTH=320,
        CLIP_MAX_MB=8,
        DISCORD_WEBHOOK_URL="https://discord.example/hook",
        GEMINI_MODEL="test-model",
        DAILY_CALL_CAP=50,
        QUOTA_TIMEZONE="America/Los_Angeles",
        MAX_ANALYSIS_AGE_MINUTES=120,
        MAX_PENDING_ANALYSES=50,
        BACKLOG_NOTICE_AT=5,
        GEMINI_COOLDOWN_SECONDS=30,
        RETRY_BACKOFF_SECONDS=10,
        DISCORD_TIMESTAMPS=False,
    )


class World:
    """Everything the sentinel owns except the camera, with Discord and Gemini faked."""

    def __init__(self, tmp_path, make_clip=clip.encode_clip):
        self.settings = make_settings(tmp_path)
        self.outbox = Outbox(":memory:")
        self.clock = Clock()
        self.posts = []
        self.edits = []
        self.described = None
        self.reporter = Reporter(
            self.outbox, self.settings, self.describe, self.post, self.edit, clock=self.clock
        )
        self.buffer = FrameBuffer(60)
        self.sink = EventSink(
            self.outbox, self.buffer, self.reporter, self.settings, executor=Inline(), make_clip=make_clip
        )
        pacers = {"discord": Pacer(self.outbox, "discord", 3), "gemini": Pacer(self.outbox, "gemini", 30)}
        common = dict(handlers=self.reporter.handlers(), pacers=pacers, clock=self.clock)
        self.dispatchers = [
            Dispatcher(self.outbox, ["discord"], **common),
            Dispatcher(self.outbox, ["gemini"], on_give_up={"analyze": self.reporter.analysis_failed}, **common),
        ]

    def describe(self, jpegs, model, reason, seconds):
        self.described = (len(jpegs), reason)
        return "A person walks in."

    def post(self, url, text, attachments=()):
        self.posts.append((text, list(attachments)))
        return f"msg-{len(self.posts)}"

    def edit(self, url, message_id, text, attachments=()):
        self.edits.append((message_id, text, list(attachments)))

    def drive(self, seconds=120):
        for _ in range(seconds):
            self.clock.now += 1
            for dispatcher in self.dispatchers:
                while dispatcher.step():
                    pass


needs_ffmpeg = pytest.mark.skipif(not clip.ffmpeg_available(), reason="ffmpeg is not installed")


@needs_ffmpeg
def test_burst_becomes_one_alert_then_one_edited_report_with_a_clip(tmp_path):
    world = World(tmp_path)
    run(BURST, world.sink, world.buffer)
    world.clock.now = START + 8
    world.drive()

    assert len(world.posts) == 1 and world.posts[0][0].startswith("Event 1 | detected ")
    assert len(world.edits) == 1
    message_id, text, attachments = world.edits[0]
    assert message_id == "msg-1"
    assert text.endswith("A person walks in.")
    assert [(name, kind) for name, _, kind in attachments] == [("event-1.mp4", "video/mp4")]
    assert attachments[0][1][4:8] == b"ftyp"
    assert world.described[0] <= world.settings.GEMINI_FRAMES
    assert world.outbox.get_event(1)["final_posted_at"] is not None
    assert world.outbox.runnable_jobs() == []


def test_clip_failure_falls_back_to_key_frames(tmp_path):
    def broken(*args):
        raise clip.ClipError("ffmpeg exploded")

    world = World(tmp_path, make_clip=broken)
    run(BURST, world.sink, world.buffer)
    world.clock.now = START + 8
    world.drive()

    _, text, attachments = world.edits[0]
    assert all(kind == "image/jpeg" for _, _, kind in attachments)
    assert len(attachments) == world.described[0]
    assert "key frames" in text


def test_event_with_no_buffered_frames_still_gets_a_report(tmp_path):
    world = World(tmp_path)
    world.sink.opened(Opened(START, "motion"))
    world.sink.closed(Closed(START, START + 1, "motion"))
    world.clock.now = START + 2
    world.drive()
    assert "no frames were buffered" in world.edits[0][1]


def test_two_bursts_make_two_events_with_ordered_slots(tmp_path):
    world = World(tmp_path)
    run(BURST + [blank()] * 5 + BURST[20:], world.sink, world.buffer)
    world.clock.now = START + 10
    world.drive()
    assert [m for m, _, _ in world.edits] == ["msg-1", "msg-2"]
