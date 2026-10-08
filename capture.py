import logging
import sys
import threading
import time

import cv2

import config

log = logging.getLogger("sentinel.capture")

MAX_READ_FAILURES = 30


class CameraError(RuntimeError):
    pass


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


def read_frames(cap, stop, clock=time.time):
    """Yield (timestamp, frame) until `stop` is set."""
    failures = 0
    while not stop.is_set():
        ok, frame = cap.read()
        if ok:
            failures = 0
            yield clock(), frame
            continue
        failures += 1
        if failures >= MAX_READ_FAILURES:
            raise CameraError("Camera stopped returning frames.")
        time.sleep(0.1)


class Capture(threading.Thread):
    """Runs `consume(frames)` on a thread so the main thread can handle signals."""

    def __init__(self, cap, consume):
        super().__init__(name="capture", daemon=True)
        self.cap = cap
        self.consume = consume
        self.stop_event = threading.Event()
        self.error = None

    def run(self):
        try:
            self.consume(read_frames(self.cap, self.stop_event))
        except Exception as exc:
            log.exception("Capture stopped")
            self.error = exc
        finally:
            self.cap.release()

    def stop(self):
        self.stop_event.set()
