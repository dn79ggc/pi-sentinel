import cv2
import numpy as np
import pytest

import clip


def jpeg_frame(value, size=(240, 320)):
    frame = np.full((*size, 3), value, dtype=np.uint8)
    return clip.encode_jpeg(frame, width=size[1], quality=80)


def test_encode_jpeg_shrinks_wide_frames():
    wide = np.zeros((720, 1280, 3), dtype=np.uint8)
    jpeg = clip.encode_jpeg(wide, width=768, quality=85)
    assert jpeg[:2] == b"\xff\xd8"
    assert cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR).shape[:2] == (432, 768)
    smaller = clip.shrink_jpeg(jpeg, width=320, quality=80)
    assert cv2.imdecode(np.frombuffer(smaller, np.uint8), cv2.IMREAD_COLOR).shape[:2] == (180, 320)


def test_shrink_rejects_garbage():
    with pytest.raises(ValueError):
        clip.shrink_jpeg(b"not a jpeg", width=320, quality=80)


def test_pick_keyframes_includes_first_and_last():
    frames = [(float(i), bytes([i])) for i in range(50)]
    picked = clip.pick_keyframes(frames, 6)
    assert len(picked) == 6
    assert picked[0] == frames[0]
    assert picked[-1] == frames[-1]
    assert [ts for ts, _ in picked] == sorted(ts for ts, _ in picked)


def test_pick_keyframes_with_few_frames_returns_all():
    frames = [(0.0, b"a"), (1.0, b"b")]
    assert clip.pick_keyframes(frames, 6) == frames
    assert clip.pick_keyframes([], 6) == []
    assert clip.pick_keyframes(frames, 0) == []


def test_input_fps_uses_real_timestamps():
    frames = [(i * 0.1, b"x") for i in range(11)]
    assert clip._input_fps(frames) == pytest.approx(10.0)
    assert clip._input_fps([(0.0, b"x")]) == 10.0


needs_ffmpeg = pytest.mark.skipif(not clip.ffmpeg_available(), reason="ffmpeg is not installed")


@needs_ffmpeg
def test_encode_clip_writes_a_playable_mp4(tmp_path):
    frames = [(i * 0.1, jpeg_frame(i * 8)) for i in range(20)]
    out = clip.encode_clip(frames, tmp_path / "event" / "clip.mp4", width=320, max_bytes=5_000_000)
    assert out.exists()
    assert out.stat().st_size > 0
    assert out.read_bytes()[4:8] == b"ftyp"


@needs_ffmpeg
def test_oversized_clip_is_reencoded_smaller(tmp_path, monkeypatch):
    calls = []
    real = clip._run_ffmpeg

    def spy(frames, out_path, width, crf, fps):
        calls.append((width, crf))
        return real(frames, out_path, width, crf, fps)

    monkeypatch.setattr(clip, "_run_ffmpeg", spy)
    frames = [(i * 0.1, jpeg_frame(i * 8)) for i in range(20)]
    clip.encode_clip(frames, tmp_path / "clip.mp4", width=320, max_bytes=1)
    assert calls == [(320, 28), (240, 35)]


def test_encode_clip_without_frames_raises(tmp_path):
    with pytest.raises(clip.ClipError):
        clip.encode_clip([], tmp_path / "clip.mp4", width=320, max_bytes=1000)


def test_missing_ffmpeg_raises_clip_error(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(clip.subprocess, "run", broken)
    with pytest.raises(clip.ClipError):
        clip.encode_clip([(0.0, b"x")], tmp_path / "clip.mp4", width=320, max_bytes=1000)
