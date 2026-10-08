import base64
from functools import lru_cache

from langchain_core.messages import HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI

PROMPT = (
    "This frame comes from a home security camera and was triggered by motion. "
    "In one or two sentences, say whether a person is present, what they are doing, "
    "and anything unusual. If nothing notable is visible, say so. "
    "Describe only what you can see."
)


def build_message(jpeg):
    encoded = base64.b64encode(jpeg).decode()
    return HumanMessage(
        content=[
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": f"data:image/jpeg;base64,{encoded}"},
        ]
    )


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


def describe(jpeg, model):
    reply = _client(model).invoke([build_message(jpeg)])
    return _as_text(reply.content).strip()
