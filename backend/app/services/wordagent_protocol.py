from __future__ import annotations

import json
from collections.abc import AsyncIterator


def event(event_type: str, **payload) -> dict:
    return {"type": event_type, **payload}


def status(content: str, loading: bool = False) -> dict:
    return event("status", content=content, loading=loading)


def text(content: str) -> dict:
    return event("text", content=content)


def done() -> dict:
    return event("done")


def error(content: str) -> dict:
    return event("error", content=content)


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def to_sse(stream: AsyncIterator[dict]) -> AsyncIterator[str]:
    async for payload in stream:
        yield sse(payload)

