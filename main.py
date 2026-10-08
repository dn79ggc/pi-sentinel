import argparse
import logging
import os
import sys
import time
from datetime import datetime

import cv2

import config
from llm import describe
from motion import MotionDetector
from notifier import post
from throttle import Throttle

log = logging.getLogger("sentinel")

MAX_READ_FAILURES = 30
REQUIRED_ENV = ("GOOGLE_API_KEY", "GEMINI_MODEL", "DISCORD_WEBHOOK_URL")


def encode_jpeg(frame):
    height, width = frame.shape[:2]
    if width > config.SEND_WIDTH:
        scale = config.SEND_WIDTH / width
        frame = cv2.resize(frame, (config.SEND_WIDTH, int(height * scale)))
    ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, config.JPEG_QUALITY])
    if not ok:
        raise RuntimeError("JPEG encoding failed")
    return buffer.tobytes()


def save_frame(jpeg):
    config.FRAME_DIR.mkdir(parents=True, exist_ok=True)
    path = config.FRAME_DIR / f"{datetime.now():%Y%m%d-%H%M%S-%f}.jpg"
    path.write_bytes(jpeg)
    return path


def report(jpeg):
    try:
        text = describe(jpeg, config.GEMINI_MODEL)
    except Exception as exc:
        log.exception("Gemini call failed")
        text = f"Motion detected. The Gemini call failed ({type(exc).__name__})."
    try:
        post(config.DISCORD_WEBHOOK_URL, text, jpeg)
    except Exception:
        log.exception("Discord post failed")


def on_motion(frame):
    jpeg = encode_jpeg(frame)
    path = save_frame(jpeg) if config.SAVE_FRAMES else None
    log.info("Motion event (saved to %s)", path)
    report(jpeg)


def process(frames, detector, throttle, on_event):
    for frame in frames:
        # The detector must see every frame, so it runs before the throttle.
        if detector.update(frame) and throttle.allow():
            on_event(frame)


def read_frames(cap):
    failures = 0
    while True:
        ok, frame = cap.read()
        if ok:
            failures = 0
            yield frame
            continue
        failures += 1
        if failures >= MAX_READ_FAILURES:
            raise SystemExit("Camera stopped returning frames.")
        time.sleep(0.1)


def open_camera():
    backend = cv2.CAP_V4L2 if sys.platform.startswith("linux") else cv2.CAP_ANY
    cap = cv2.VideoCapture(config.CAMERA_INDEX, backend)
    if not cap.isOpened():
        raise SystemExit(
            f"Could not open camera index {config.CAMERA_INDEX}. "
            "On Linux, list devices with: v4l2-ctl --list-devices"
        )
    # MJPG is needed for usable frame rates at 720p on most USB webcams.
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAPTURE_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAPTURE_HEIGHT)
    return cap


def run_camera():
    cap = open_camera()
    detector = MotionDetector(min_fraction=config.MOTION_MIN_FRACTION)
    throttle = Throttle(config.COOLDOWN_SECONDS, config.DAILY_CALL_CAP)
    log.info("Watching camera %d. Press Ctrl+C to stop.", config.CAMERA_INDEX)
    try:
        process(read_frames(cap), detector, throttle, on_motion)
    except KeyboardInterrupt:
        log.info("Stopped.")
    finally:
        cap.release()


def run_test_image(path):
    with open(path, "rb") as handle:
        report(handle.read())


def main():
    parser = argparse.ArgumentParser(description="Motion-triggered camera alerts with Gemini.")
    parser.add_argument(
        "--test-image",
        metavar="PATH",
        help="send a JPEG through Gemini and Discord without opening the camera",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        raise SystemExit(f"Missing settings in .env: {', '.join(missing)}")

    if args.test_image:
        run_test_image(args.test_image)
    else:
        run_camera()


if __name__ == "__main__":
    main()
