# pi-sentinel

A USB webcam watches a room. When something moves, the program saves a short clip (with a few seconds from before the motion), asks Gemini through LangChain what happened, and reports to a Discord channel.

It is built for a Raspberry Pi 5, but it runs on any machine with Python 3.10 or newer, ffmpeg, and a webcam.

Status: v0.2a. Camera, motion events with pre-roll and post-roll clips, a paced queue for Gemini and Discord, and edited Discord reports work. Person detection, a systemd service, microphone, and wake word are planned (see Roadmap).

## How it works

```
webcam -> motion detector -> event tracker -> clip + key frames -> queue -> Gemini -> Discord
              |                                       ^
              +--> ring buffer of recent frames ------+
```

Each event produces one Discord message that changes over time:

1. When motion starts, the message is posted at once:

   ```
   Event 12 | detected 14:32:05 | motion
   Clip and analysis to follow.
   ```

2. When the clip is ready and Gemini has answered, the same message is edited and the clip is attached:

   ```
   Event 12 | detected 14:32:05 | 14 s long | posted 14:33:10 (65 s after)
   A person walks in and sets a bag down.
   ```

Times use Discord's timestamp markup, so each viewer sees their own timezone. Set `DISCORD_TIMESTAMPS=0` for plain Pi-local times.

Files:

- `capture.py` reads the webcam on its own thread.
- `motion.py` compares each frame with a slowly updating background.
- `events.py` turns motion yes/no into "event opened" and "event closed", with post-roll and a maximum length.
- `buffer.py` keeps the last minute or so of JPEG frames in memory, which is where the pre-roll comes from.
- `clip.py` builds the MP4 with ffmpeg and picks the key frames sent to Gemini.
- `pipeline.py` connects the pieces and builds each clip on a worker thread.
- `outbox.py` stores events, queued jobs, and pacing state in SQLite (`data/sentinel.db`), so a restart loses nothing.
- `scheduler.py` runs queued jobs in order, spaced by a cooldown per service, with retries.
- `reporter.py` does the work for each job: post the alert, call Gemini, edit the message.
- `llm.py` sends the key frames and a fixed prompt to Gemini.
- `notifier.py` posts and edits Discord messages and builds the message text.
- `main.py` starts everything and handles Ctrl+C and `systemctl stop`.

## The queue

Events are never dropped for arriving too fast. Each one waits for its slot:

- Gemini calls are spaced by `GEMINI_COOLDOWN_SECONDS`, in event order. Alerts are spaced by `ALERT_MIN_GAP_SECONDS` on a separate thread, so alerts do not wait for Gemini.
- If 5 or more analyses are waiting, one notice says how far behind the queue is. It is not repeated until the queue drains.
- An analysis is skipped, with a visible note in the report, when the event is older than `MAX_ANALYSIS_AGE_MINUTES`, when `DAILY_CALL_CAP` is reached, or when `MAX_PENDING_ANALYSES` are already waiting. The alert and the clip are still posted.
- A failed call is retried with a growing delay, up to `MAX_RETRIES` times. After that the report says the analysis failed.
- If Discord rejects an edit, the report is posted as a new message instead.
- The daily cap resets at midnight Pacific time, which is when Google resets daily request quotas.

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

On a Raspberry Pi, install the system tools first: `sudo apt install -y python3-venv v4l-utils ffmpeg`. Without ffmpeg the program still runs, but reports carry key frames instead of a clip.

Fill in `.env`:

| Setting | Where to get it |
|---|---|
| `GOOGLE_API_KEY` | Google AI Studio |
| `GEMINI_MODEL` | A current model name from the Gemini API documentation. Names change, so none is hardcoded. |
| `DISCORD_WEBHOOK_URL` | Discord channel settings, Integrations, Webhooks |

`.env` is listed in `.gitignore`. Never commit it. If a key or webhook URL is ever committed, revoke it and create a new one.

## Run

Check that your webhook can edit a message and attach a file to the edit. This is what every report relies on:

```bash
python scripts/check_webhook_edit.py
```

It posts a message, edits it with a picture, and prints PASS or FAIL. Add `--cleanup` to delete the test message afterwards.

Check the cloud half without a camera. Use any JPEG:

```bash
python main.py --test-image path/to/photo.jpg
```

Then start the camera loop:

```bash
python main.py
```

Wave at the camera. Stop with Ctrl+C. Clips and key frames go to `data/clips/`, one folder per event, which is gitignored.

If the camera does not open, list devices with `v4l2-ctl --list-devices` (Linux) and set `CAMERA_INDEX` in `.env` to the right number.

## Tests

```bash
pip install pytest
python -m pytest
```

The tests use synthetic frames, make no network calls, and replay time with a fake clock. The clip tests use ffmpeg and are skipped when it is not installed.

## Settings

All optional, set in `.env`:

| Setting | Default | Meaning |
|---|---|---|
| `CAMERA_INDEX` | 0 | Which camera to open |
| `MOTION_MIN_FRACTION` | 0.01 | Share of the frame that must change to count as motion. Raise it if shadows or noise trigger events. |
| `PRE_ROLL_SECONDS` | 5 | Seconds before the first motion included in the clip |
| `POST_ROLL_SECONDS` | 5 | Seconds without motion before an event ends. Raise it if one visit splits into several events. |
| `MAX_EVENT_SECONDS` | 60 | An event is cut off at this length and a new one starts if motion continues |
| `BUFFER_FPS` | 10 | Frames per second kept for clips |
| `BUFFER_WIDTH` | 960 | Width of buffered frames |
| `GEMINI_FRAMES` | 6 | Key frames sent to Gemini per event |
| `CLIP_WIDTH` | 640 | Width of the clip |
| `CLIP_MAX_MB` | 8 | Clips over this are re-encoded once, then left on the Pi instead of uploaded |
| `CLIP_DIR` | data/clips | Where clips and key frames are saved |
| `ALERT_MIN_GAP_SECONDS` | 3 | Minimum gap between Discord posts |
| `GEMINI_COOLDOWN_SECONDS` | 30 | Minimum gap between Gemini calls |
| `DAILY_CALL_CAP` | 50 | Maximum Gemini calls per day. Check your real limit in Google AI Studio. |
| `QUOTA_TIMEZONE` | America/Los_Angeles | Timezone for the daily reset |
| `MAX_RETRIES` | 5 | Tries per job before giving up |
| `RETRY_BACKOFF_SECONDS` | 10 | First retry delay, doubled each time |
| `MAX_PENDING_ANALYSES` | 50 | Analyses allowed to wait at once |
| `BACKLOG_NOTICE_AT` | 5 | Waiting analyses that trigger the backlog notice |
| `MAX_ANALYSIS_AGE_MINUTES` | 120 | Older events are reported without analysis |
| `THROTTLE_POLICY` | defer | `defer` queues work that has to wait. `drop` skips it, which is meant for live watch mode later. |
| `DISCORD_TIMESTAMPS` | 1 | Set to 0 for plain Pi-local times in messages |
| `DB_PATH` | data/sentinel.db | SQLite file for events and the queue |

## Limits

- Motion is the only trigger, so Gemini also receives clips where the motion was a curtain, a pet, or a lighting change. The prompt asks it to say whether a person is present.
- Gemini's descriptions can be wrong. The clip is attached to every report so you can check.
- Frames leave your network. Read the [Gemini API terms](https://ai.google.dev/terms) for the tier you use before pointing the camera at private spaces.
- If the Pi loses power or the program is killed mid-event, that event is reported as cut short, without a clip.
- Discord normally does not notify again for an edit, so you are alerted once at detection and the analysis fills in quietly. `scripts/check_webhook_edit.py` lets you confirm this on your phone.

## Roadmap

1. v0.2b: person and approach detection, so people are labeled in alerts
2. v0.2c: retention cleanup and a systemd service for starting on boot
3. v0.3: Discord bot with slash commands, and a digest that lets several waiting events share one Gemini request
4. v0.4: live watch mode, toggled by the HAT button and by a Discord command
5. v0.5: microphone with voice activity detection
6. v0.6: wake word, then the command goes to Gemini

## License

MIT. See `LICENSE`.
