# pi-sentinel

A USB webcam watches a room. When something moves, the program sends one frame to Gemini through LangChain and posts Gemini's description, with the frame attached, to a Discord channel.

It is built for a Raspberry Pi 5, but it runs on any machine with Python 3.10 or newer and a webcam.

Status: MVP. Camera, motion trigger, Gemini, and Discord work. Microphone, wake word, person detection, and clip recording are planned (see Roadmap).

## How it works

```
webcam -> motion detector -> throttle -> JPEG -> Gemini -> Discord webhook
```

- `motion.py` compares each frame with a slowly updating background and flags frames where enough of the image changed.
- `throttle.py` enforces a cooldown between events and a daily cap, because every event is one Gemini call and the free tier has a small quota.
- `llm.py` sends the frame and a fixed prompt to Gemini.
- `notifier.py` posts the answer and the frame to a Discord webhook.
- `main.py` runs the capture loop. It is single-threaded, so frames wait while a Gemini call is in flight.

## Setup

Linux or macOS:

```bash
git clone git@github.com:USERNAME/pi-sentinel.git
cd pi-sentinel
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1` and copy with `Copy-Item .env.example .env`.

On a Raspberry Pi, install the system tools first: `sudo apt install -y python3-venv v4l-utils`.

Fill in `.env`:

| Setting | Where to get it |
|---|---|
| `GOOGLE_API_KEY` | Google AI Studio |
| `GEMINI_MODEL` | A current model name from the Gemini API documentation. Names change, so none is hardcoded. |
| `DISCORD_WEBHOOK_URL` | Discord channel settings, Integrations, Webhooks |

`.env` is listed in `.gitignore`. Never commit it. If a key or webhook URL is ever committed, revoke it and create a new one.

## Run

Check the cloud half first, without a camera. Use any JPEG:

```bash
python main.py --test-image path/to/photo.jpg
```

A Discord message with the photo and a description should appear within a few seconds.

Then start the camera loop:

```bash
python main.py
```

Wave at the camera. Stop with Ctrl+C. Saved frames go to `data/frames/`, which is gitignored.

If the camera does not open, list devices with `v4l2-ctl --list-devices` (Linux) and set `CAMERA_INDEX` in `.env` to the right number.

## Tests

```bash
pip install pytest
python -m pytest
```

The tests use synthetic frames and make no network calls.

## Settings

All optional, set in `.env`:

| Setting | Default | Meaning |
|---|---|---|
| `CAMERA_INDEX` | 0 | Which camera to open |
| `MOTION_MIN_FRACTION` | 0.01 | Share of the frame that must change to count as motion. Raise it if shadows or noise trigger alerts. |
| `COOLDOWN_SECONDS` | 30 | Minimum gap between alerts |
| `DAILY_CALL_CAP` | 50 | Maximum Gemini calls per day |
| `SAVE_FRAMES` | 1 | Set to 0 to stop saving frames to disk |

## Limits

- Motion is the only trigger, so Gemini also receives frames where the motion was a curtain, a pet, or a lighting change. The prompt asks it to say whether a person is present.
- Gemini's descriptions can be wrong. The frame is attached to every alert so you can check.
- Frames leave your network. Read the [Gemini API terms](https://ai.google.dev/terms) for the tier you use before pointing the camera at private spaces.

## Roadmap

1. Person detection, so only people trigger Gemini
2. Short clips with a few seconds of pre-roll
3. Microphone with voice activity detection
4. Wake-word commands sent to Gemini
5. A systemd service for starting on boot

## License

MIT. See `LICENSE`.
