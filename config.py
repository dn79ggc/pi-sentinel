import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _int(name, default):
    return int(os.getenv(name, default))


def _float(name, default):
    return float(os.getenv(name, default))


def _flag(name, default):
    return os.getenv(name, default) == "1"


CAMERA_INDEX = _int("CAMERA_INDEX", 0)
CAPTURE_WIDTH = 1280
CAPTURE_HEIGHT = 720

# Share of the downscaled frame that must change to count as motion.
MOTION_MIN_FRACTION = _float("MOTION_MIN_FRACTION", 0.01)

# Event window: an event starts at the first motion frame, ends after
# POST_ROLL_SECONDS without motion, and is cut off at MAX_EVENT_SECONDS.
PRE_ROLL_SECONDS = _float("PRE_ROLL_SECONDS", 5)
POST_ROLL_SECONDS = _float("POST_ROLL_SECONDS", 5)
MAX_EVENT_SECONDS = _float("MAX_EVENT_SECONDS", 60)

# Frames kept in memory for clips. Capture runs faster than BUFFER_FPS.
BUFFER_FPS = _float("BUFFER_FPS", 10)
BUFFER_WIDTH = _int("BUFFER_WIDTH", 960)
BUFFER_JPEG_QUALITY = 80

GEMINI_FRAMES = _int("GEMINI_FRAMES", 6)
SEND_WIDTH = 768
JPEG_QUALITY = 85

CLIP_WIDTH = _int("CLIP_WIDTH", 640)
CLIP_MAX_MB = _float("CLIP_MAX_MB", 8)
CLIP_DIR = Path(os.getenv("CLIP_DIR", "data/clips"))

# Discord and Gemini are paced separately. A job that cannot run yet waits for its slot.
ALERT_MIN_GAP_SECONDS = _float("ALERT_MIN_GAP_SECONDS", 3)
GEMINI_COOLDOWN_SECONDS = _float("GEMINI_COOLDOWN_SECONDS", 30)
DAILY_CALL_CAP = _int("DAILY_CALL_CAP", 50)
# Google resets daily request quotas at midnight Pacific time.
QUOTA_TIMEZONE = os.getenv("QUOTA_TIMEZONE", "America/Los_Angeles")

MAX_RETRIES = _int("MAX_RETRIES", 5)
RETRY_BACKOFF_SECONDS = _float("RETRY_BACKOFF_SECONDS", 10)
MAX_PENDING_ANALYSES = _int("MAX_PENDING_ANALYSES", 50)
BACKLOG_NOTICE_AT = _int("BACKLOG_NOTICE_AT", 5)
MAX_ANALYSIS_AGE_MINUTES = _float("MAX_ANALYSIS_AGE_MINUTES", 120)
# "defer" queues over-limit work. "drop" is reserved for live watch mode.
THROTTLE_POLICY = os.getenv("THROTTLE_POLICY", "defer")

# Discord renders <t:UNIX:T> in each viewer's timezone. Set to 0 for plain Pi-local times.
DISCORD_TIMESTAMPS = _flag("DISCORD_TIMESTAMPS", "1")

DB_PATH = Path(os.getenv("DB_PATH", "data/sentinel.db"))

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
