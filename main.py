import argparse
import logging
import os
import signal
import threading
import time
from pathlib import Path

import config
from buffer import FrameBuffer
from capture import Capture, open_camera
from clip import encode_jpeg, ffmpeg_available
from events import EventTracker
from llm import describe
from motion import MotionDetector
from notifier import edit_message, post_message
from outbox import Outbox
from pipeline import EventSink, process
from reporter import Reporter
from scheduler import Dispatcher, Pacer

log = logging.getLogger("sentinel")

REQUIRED_ENV = ("GOOGLE_API_KEY", "GEMINI_MODEL", "DISCORD_WEBHOOK_URL")


def build_dispatchers(outbox, reporter):
    pacers = {
        "discord": Pacer(outbox, "discord", config.ALERT_MIN_GAP_SECONDS),
        "gemini": Pacer(outbox, "gemini", config.GEMINI_COOLDOWN_SECONDS),
    }
    common = dict(
        handlers=reporter.handlers(),
        pacers=pacers,
        max_retries=config.MAX_RETRIES,
        backoff=config.RETRY_BACKOFF_SECONDS,
        on_give_up={"analyze": reporter.analysis_failed},
    )
    # Separate threads, so a slow Gemini call never delays an alert.
    discord = Dispatcher(outbox, ["discord"], **common)
    gemini = Dispatcher(
        outbox,
        ["gemini"],
        policy=config.THROTTLE_POLICY,
        on_drop={"analyze": reporter.analysis_dropped},
        **common,
    )
    return discord, gemini


def run_camera():
    if not ffmpeg_available():
        log.warning("ffmpeg was not found, so events will send key frames instead of clips. Install: sudo apt install ffmpeg")

    outbox = Outbox(config.DB_PATH)
    reporter = Reporter(outbox, config, describe, post_message, edit_message)
    interrupted = outbox.recover(time.time())
    if interrupted:
        log.warning("Events cut short by the last shutdown: %s", interrupted)

    buffer = FrameBuffer(config.PRE_ROLL_SECONDS + config.MAX_EVENT_SECONDS + config.POST_ROLL_SECONDS + 10)
    tracker = EventTracker(config.POST_ROLL_SECONDS, config.MAX_EVENT_SECONDS)
    detector = MotionDetector(min_fraction=config.MOTION_MIN_FRACTION)
    sink = EventSink(outbox, buffer, reporter, config)

    def consume(frames):
        process(
            frames,
            detector,
            tracker,
            buffer,
            sink,
            buffer_interval=1 / config.BUFFER_FPS,
            encode=lambda frame: encode_jpeg(frame, config.BUFFER_WIDTH, config.BUFFER_JPEG_QUALITY),
        )

    stop = threading.Event()
    for name in (signal.SIGINT, signal.SIGTERM):
        signal.signal(name, lambda *_: stop.set())

    capture = Capture(open_camera(), consume)
    workers = [
        threading.Thread(target=dispatcher.run, args=(stop,), name=label, daemon=True)
        for dispatcher, label in zip(build_dispatchers(outbox, reporter), ("discord", "gemini"))
    ]
    for worker in workers:
        worker.start()
    capture.start()
    log.info("Watching camera %d. Press Ctrl+C to stop.", config.CAMERA_INDEX)

    while not stop.is_set() and capture.is_alive():
        stop.wait(1)

    capture.stop()
    capture.join(timeout=10)
    sink.shutdown()
    stop.set()
    for worker in workers:
        worker.join(timeout=10)
    outbox.close()
    log.info("Stopped.")
    if capture.error:
        raise SystemExit(str(capture.error))


def run_test_image(path):
    jpeg = Path(path).read_bytes()
    text = describe([jpeg], config.GEMINI_MODEL, reason="a test image")
    post_message(config.DISCORD_WEBHOOK_URL, text, [("test.jpg", jpeg, "image/jpeg")])
    log.info("Posted the test image.")


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
