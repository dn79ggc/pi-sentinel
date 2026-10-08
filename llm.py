import base64
from functools import lru_cache

from langchain_core.messages import HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI

PROMPT = (
    "These {count} frames are in time order from one clip recorded by a home security camera. "
    "The camera started recording because of {reason}.{length} "
    "In two or three sentences, say whether a person is present, what they do across the frames, "
    "and anything unusual. If nothing notable is visible, say so. "
    "Describe only what you can see."
)


def build_message(jpegs, reason="motion", seconds=None):
    length = f" The clip is about {round(seconds)} seconds long." if seconds else ""
    content = [{"type": "text", "text": PROMPT.format(count=len(jpegs), reason=reason, length=length)}]
    for jpeg in jpegs:
        encoded = base64.b64encode(jpeg).decode()
        content.append({"type": "image_url", "image_url": f"data:image/jpeg;base64,{encoded}"})
    return HumanMessage(content=content)


def _as_text(content):
    # Newer langchain versions can return a list of content blocks.
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)


@lru_cache(maxsize=1)
def _client(model):
    # Reads GOOGLE_API_KEY from the environment.
    return ChatGoogleGenerativeAI(model=model)


def describe(jpegs, model, reason="motion", seconds=None):
    reply = _client(model).invoke([build_message(jpegs, reason, seconds)])
    return _as_text(reply.content).strip()


def is_quota_error(exc):
    # The Google client wraps HTTP 429 in several exception types, so match on the text.
    text = f"{type(exc).__name__} {exc}".lower()
    return "429" in text or "resource_exhausted" in text or "resourceexhausted" in text or "quota" in text
