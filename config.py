import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _int(name, default):
    return int(os.getenv(name, default))


def _float(name, default):
    return float(os.getenv(name, default))


CAMERA_INDEX = _int("CAMERA_INDEX", 0)
CAPTURE_WIDTH = 1280
CAPTURE_HEIGHT = 720

# Share of the downscaled frame that must change to count as motion.
MOTION_MIN_FRACTION = _float("MOTION_MIN_FRACTION", 0.01)

# Every allowed event is one Gemini call, so these two protect the free-tier quota.
COOLDOWN_SECONDS = _float("COOLDOWN_SECONDS", 30)
DAILY_CALL_CAP = _int("DAILY_CALL_CAP", 50)

SEND_WIDTH = 768
JPEG_QUALITY = 85

SAVE_FRAMES = os.getenv("SAVE_FRAMES", "1") == "1"
FRAME_DIR = Path(os.getenv("FRAME_DIR", "data/frames"))

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
