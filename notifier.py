import json
from datetime import datetime

import requests

CONTENT_LIMIT = 1900  # Discord rejects content over 2000 characters.


class DiscordError(RuntimeError):
    pass


class RateLimited(DiscordError):
    def __init__(self, retry_after):
        super().__init__(f"Discord rate limit, retry in {retry_after:.1f} s")
        self.retry_after = retry_after


def _check(response):
    if response.status_code == 429:
        try:
            retry_after = float(response.json().get("retry_after", 1))
        except ValueError:
            retry_after = float(response.headers.get("Retry-After", 1))
        raise RateLimited(retry_after)
    if response.status_code >= 400:
        raise DiscordError(f"Discord returned {response.status_code}: {response.text[:200]}")


def _files(attachments):
    return {
        f"files[{index}]": (name, data, content_type)
        for index, (name, data, content_type) in enumerate(attachments)
    }


def post_message(webhook_url, text, attachments=(), timeout=60):
    """Post a message and return its id. Attachments are (filename, bytes, content_type) tuples."""
    response = requests.post(
        webhook_url,
        params={"wait": "true"},
        data={"content": text[:CONTENT_LIMIT]},
        files=_files(attachments) or None,
        timeout=timeout,
    )
    _check(response)
    return response.json()["id"]


def edit_message(webhook_url, message_id, text, attachments=(), timeout=120):
    url = f"{webhook_url.rstrip('/')}/messages/{message_id}"
    content = text[:CONTENT_LIMIT]
    if attachments:
        # With API v10, `attachments` must list every file the message ends up with.
        payload = {
            "content": content,
            "attachments": [{"id": i, "filename": name} for i, (name, _, _) in enumerate(attachments)],
        }
        response = requests.patch(
            url, data={"payload_json": json.dumps(payload)}, files=_files(attachments), timeout=timeout
        )
    else:
        response = requests.patch(url, json={"content": content}, timeout=timeout)
    _check(response)


def stamp(timestamp, discord_time=True):
    if discord_time:
        return f"<t:{int(timestamp)}:T>"
    return datetime.fromtimestamp(timestamp).strftime("%H:%M:%S")


def _elapsed(seconds):
    seconds = max(0, round(seconds))
    return f"{seconds} s" if seconds < 120 else f"{seconds // 60} min"


def alert_text(event_id, detected_at, reason, discord_time=True):
    return (
        f"Event {event_id} | detected {stamp(detected_at, discord_time)} | {reason}\n"
        "Clip and analysis to follow."
    )


def report_text(event_id, detected_at, posted_at, length_seconds, body, discord_time=True):
    parts = [f"Event {event_id}", f"detected {stamp(detected_at, discord_time)}"]
    if length_seconds:
        parts.append(f"{round(length_seconds)} s long")
    after = _elapsed(posted_at - detected_at)
    parts.append(f"posted {stamp(posted_at, discord_time)} ({after} after)")
    head = " | ".join(parts)
    return f"{head}\n{body}"[:CONTENT_LIMIT]


def backlog_text(waiting, minutes_behind):
    return f"Backlog: {waiting} analyses waiting, about {minutes_behind:.1f} min behind."
