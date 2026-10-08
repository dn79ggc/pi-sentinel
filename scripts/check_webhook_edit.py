"""One-minute check that your webhook can edit a message and attach a file to the edit.

Run from the project folder:  python scripts/check_webhook_edit.py
Add --cleanup to delete the test message afterwards.
"""
import argparse
import sys
import time
from pathlib import Path

import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from clip import encode_jpeg  # noqa: E402
from notifier import DiscordError, edit_message, post_message  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cleanup", action="store_true", help="delete the test message at the end")
    args = parser.parse_args()

    url = config.DISCORD_WEBHOOK_URL
    if not url:
        raise SystemExit("DISCORD_WEBHOOK_URL is not set in .env")

    picture = np.full((120, 160, 3), (40, 140, 220), dtype=np.uint8)
    jpeg = encode_jpeg(picture, width=160, quality=85)

    message_id = post_message(url, "Webhook check: this is the original text.")
    print(f"1. Posted message {message_id}. Watch your phone: this one should notify you.")
    time.sleep(5)

    try:
        edit_message(url, message_id, "Webhook check: edited, with a picture attached.", [("check.jpg", jpeg, "image/jpeg")])
    except DiscordError as exc:
        print(f"2. FAIL: the edit was rejected: {exc}")
        return 1
    print("2. Edit accepted. Did your phone notify you again? (It should not.)")

    shown = requests.get(f"{url.rstrip('/')}/messages/{message_id}", timeout=15).json()
    names = [item.get("filename") for item in shown.get("attachments", [])]
    text_ok = shown.get("content", "").startswith("Webhook check: edited")
    print(f"3. Text changed: {text_ok}. Attachments on the message: {names}")

    if args.cleanup:
        requests.delete(f"{url.rstrip('/')}/messages/{message_id}", timeout=15)
        print("4. Test message deleted.")

    if text_ok and names == ["check.jpg"]:
        print("PASS: editing with a file works. Option 3 will work as designed.")
        return 0
    print("FAIL: the edit went through but the text or the file is missing. Send me the lines above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
