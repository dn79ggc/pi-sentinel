import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np


class ClipError(RuntimeError):
    pass


def encode_jpeg(frame, width, quality):
    height, current = frame.shape[:2]
    if current > width:
        frame = cv2.resize(frame, (width, int(height * width / current)))
    ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("JPEG encoding failed")
    return buffer.tobytes()


def shrink_jpeg(jpeg, width, quality):
    frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("not a decodable JPEG")
    return encode_jpeg(frame, width, quality)


def pick_keyframes(frames, count):
    """Evenly spaced frames, always including the first and the last."""
    if count <= 0 or not frames:
        return []
    if len(frames) <= count:
        return list(frames)
    if count == 1:
        return [frames[len(frames) // 2]]
    last = len(frames) - 1
    indexes = sorted({round(i * last / (count - 1)) for i in range(count)})
    return [frames[i] for i in indexes]


def ffmpeg_available():
    return shutil.which("ffmpeg") is not None


def _input_fps(frames):
    # Buffered frames have uneven timestamps, so use the real average rate for true-speed playback.
    span = frames[-1][0] - frames[0][0]
    if len(frames) < 2 or span <= 0:
        return 10.0
    return min(30.0, max(1.0, (len(frames) - 1) / span))


def _run_ffmpeg(frames, out_path, width, crf, fps):
    command = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "image2pipe", "-framerate", f"{fps:.3f}", "-c:v", "mjpeg", "-i", "-",
        "-vf", f"scale={width}:-2",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(out_path),
    ]
    stream = b"".join(jpeg for _, jpeg in frames)
    try:
        result = subprocess.run(command, input=stream, capture_output=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ClipError(f"ffmpeg did not run: {exc}") from exc
    if result.returncode != 0:
        raise ClipError(result.stderr.decode(errors="replace").strip()[-300:])


def encode_clip(frames, out_path, width, max_bytes):
    """Write an H.264 MP4. A file over max_bytes is re-encoded once, smaller and more compressed."""
    if not frames:
        raise ClipError("no frames to encode")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fps = _input_fps(frames)
    _run_ffmpeg(frames, out_path, width, 28, fps)
    if out_path.stat().st_size > max_bytes:
        _run_ffmpeg(frames, out_path, max(160, int(width * 0.75) // 2 * 2), 35, fps)
    return out_path
