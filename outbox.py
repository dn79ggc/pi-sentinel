import json
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at REAL NOT NULL,
    ended_at REAL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    clip_path TEXT,
    keyframes TEXT NOT NULL DEFAULT '[]',
    clip_seconds REAL,
    message_id TEXT,
    analysis TEXT,
    note TEXT,
    final_posted_at REAL
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER,
    kind TEXT NOT NULL,
    due REAL NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'pending',
    payload TEXT,
    last_error TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# analyze and finish edit the alert message, so they wait until the alert has been tried.
_RUNNABLE = """
SELECT * FROM jobs
WHERE state = 'pending'
  AND NOT (
    kind IN ('analyze', 'finish')
    AND EXISTS (
        SELECT 1 FROM jobs alert
        WHERE alert.event_id = jobs.event_id AND alert.kind = 'alert' AND alert.state = 'pending'
    )
  )
ORDER BY due, id
"""


class Outbox:
    """SQLite store for events, queued jobs and pacing state, so a restart loses nothing."""

    def __init__(self, path):
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(SCHEMA)

    def _run(self, sql, params=()):
        with self._lock:
            return self._db.execute(sql, params)

    def _one(self, sql, params=()):
        with self._lock:
            row = self._db.execute(sql, params).fetchone()
        return dict(row) if row else None

    def _all(self, sql, params=()):
        with self._lock:
            return [dict(row) for row in self._db.execute(sql, params).fetchall()]

    def close(self):
        with self._lock:
            self._db.close()

    def create_event(self, detected_at, reason):
        cursor = self._run(
            "INSERT INTO events (detected_at, reason, status) VALUES (?, ?, 'open')",
            (detected_at, reason),
        )
        return cursor.lastrowid

    def get_event(self, event_id):
        event = self._one("SELECT * FROM events WHERE id = ?", (event_id,))
        if event:
            event["keyframes"] = json.loads(event["keyframes"])
        return event

    def close_event(self, event_id, ended_at):
        self._run("UPDATE events SET status = 'closed', ended_at = ? WHERE id = ?", (ended_at, event_id))

    def set_clip(self, event_id, clip_path, keyframes, clip_seconds):
        self._run(
            "UPDATE events SET clip_path = ?, keyframes = ?, clip_seconds = ? WHERE id = ?",
            (str(clip_path) if clip_path else None, json.dumps([str(k) for k in keyframes]), clip_seconds, event_id),
        )

    def set_message_id(self, event_id, message_id):
        self._run("UPDATE events SET message_id = ? WHERE id = ?", (message_id, event_id))

    def set_analysis(self, event_id, text):
        self._run("UPDATE events SET analysis = ? WHERE id = ?", (text, event_id))

    def set_note(self, event_id, note):
        self._run("UPDATE events SET note = ? WHERE id = ?", (note, event_id))

    def set_final_posted(self, event_id, posted_at):
        self._run("UPDATE events SET final_posted_at = ? WHERE id = ?", (posted_at, event_id))

    def add_job(self, kind, due, event_id=None, payload=None):
        cursor = self._run(
            "INSERT INTO jobs (event_id, kind, due, payload) VALUES (?, ?, ?, ?)",
            (event_id, kind, due, payload),
        )
        return cursor.lastrowid

    def runnable_jobs(self):
        return self._all(_RUNNABLE)

    def update_job(self, job_id, **fields):
        names = ", ".join(f"{name} = ?" for name in fields)
        self._run(f"UPDATE jobs SET {names} WHERE id = ?", (*fields.values(), job_id))

    def count_pending(self, kind):
        row = self._one("SELECT COUNT(*) AS n FROM jobs WHERE kind = ? AND state = 'pending'", (kind,))
        return row["n"]

    def pending_clip_paths(self):
        rows = self._all(
            "SELECT clip_path FROM events WHERE clip_path IS NOT NULL AND id IN "
            "(SELECT event_id FROM jobs WHERE state = 'pending' AND event_id IS NOT NULL)"
        )
        return [row["clip_path"] for row in rows]

    def get_meta(self, key, default=None):
        row = self._one("SELECT value FROM meta WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_meta(self, key, value):
        self._run(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )

    def get_pace(self, channel):
        value = self.get_meta(f"pace:{channel}")
        return float(value) if value is not None else None

    def set_pace(self, channel, timestamp):
        self.set_meta(f"pace:{channel}", timestamp)

    def calls_on(self, day):
        return int(self.get_meta(f"calls:{day}", 0))

    def add_call(self, day):
        with self._lock:
            self.set_meta(f"calls:{day}", self.calls_on(day) + 1)

    def recover(self, now):
        """Report events whose clip step never finished, because a restart cut it short."""
        stale = self._all(
            "SELECT id FROM events WHERE status IN ('open', 'closed') AND final_posted_at IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM jobs WHERE jobs.event_id = events.id AND jobs.kind IN ('analyze', 'finish'))"
        )
        for row in stale:
            self._run("UPDATE events SET status = 'interrupted' WHERE id = ?", (row["id"],))
            self.set_note(row["id"], "No clip: the sentinel restarted before this event finished.")
            self.add_job("finish", now, event_id=row["id"])
        return [row["id"] for row in stale]
