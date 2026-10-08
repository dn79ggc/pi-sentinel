import numpy as np

import llm
from main import encode_jpeg, process
from motion import MotionDetector
from throttle import Throttle


def blank():
    return np.full((240, 320, 3), 80, dtype=np.uint8)


def moving():
    frame = blank()
    frame[60:160, 100:200] = 255
    return frame


def test_process_fires_once_per_cooldown_window():
    frames = [blank()] * 10 + [moving()] * 3 + [blank()] * 3
    events = []
    process(
        frames,
        MotionDetector(warmup_frames=5),
        Throttle(cooldown=3600, daily_cap=10),
        events.append,
    )
    assert len(events) == 1


def test_process_skips_static_footage():
    events = []
    process(
        [blank()] * 30,
        MotionDetector(warmup_frames=5),
        Throttle(cooldown=0, daily_cap=10),
        events.append,
    )
    assert events == []


def test_encode_jpeg_shrinks_wide_frames():
    wide = np.zeros((720, 1280, 3), dtype=np.uint8)
    jpeg = encode_jpeg(wide)
    assert jpeg[:2] == b"\xff\xd8"


def test_build_message_carries_prompt_and_image():
    message = llm.build_message(b"fake-jpeg-bytes")
    kinds = [part["type"] for part in message.content]
    assert kinds == ["text", "image_url"]
    assert message.content[1]["image_url"].startswith("data:image/jpeg;base64,")


def test_as_text_joins_block_lists():
    blocks = [{"type": "text", "text": "A person "}, {"type": "text", "text": "waves."}]
    assert llm._as_text(blocks) == "A person waves."
