import numpy as np

from motion import MotionDetector


def blank(value=80):
    return np.full((240, 320, 3), value, dtype=np.uint8)


def with_block(size):
    frame = blank()
    frame[60 : 60 + size, 100 : 100 + size] = 255
    return frame


def settled_detector():
    detector = MotionDetector(warmup_frames=5)
    for _ in range(10):
        detector.update(blank())
    return detector


def test_static_scene_is_not_motion():
    detector = settled_detector()
    assert detector.update(blank()) is False


def test_large_change_is_motion():
    detector = settled_detector()
    assert detector.update(with_block(100)) is True


def test_tiny_change_is_ignored():
    detector = settled_detector()
    assert detector.update(with_block(4)) is False


def test_no_motion_reported_during_warmup():
    detector = MotionDetector(warmup_frames=5)
    detector.update(blank())
    assert detector.update(with_block(100)) is False
