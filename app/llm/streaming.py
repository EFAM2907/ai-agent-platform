"""
StreamUsageCollector: a small mutable box a provider fills in while
streaming, so the caller can find out token usage and finish_reason
*after* the stream ends -- without changing what gets yielded to the
caller (plain text chunks stay plain text chunks).

A fresh instance is created per call (never reused or shared across
concurrent streams), so there's no risk of one request's usage data
leaking into another's.
"""

from __future__ import annotations

from app.llm.schemas import LLMResponse


class StreamUsageCollector:
    def __init__(self) -> None:
        self.final_response: LLMResponse | None = None