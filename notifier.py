import requests


def post(webhook_url, text, jpeg=None, timeout=15):
    # Discord rejects message content over 2000 characters.
    data = {"content": text[:1900]}
    files = {"files[0]": ("frame.jpg", jpeg, "image/jpeg")} if jpeg else None
    response = requests.post(webhook_url, data=data, files=files, timeout=timeout)
    response.raise_for_status()
