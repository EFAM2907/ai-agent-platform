"""
CostLogger: persists every LLMResponse as a JSON line in a local
.jsonl file.

JSONL was chosen deliberately for where the project stands right
now: each call is an independent, append-only event, with no need
for a database migration before you can start measuring real cost.
When Phase 6 (analytics) arrives, these same records can be read
line by line and inserted into Postgres without losing any data --
the shape of each line is already the final shape that table would
have.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.llm.schemas import LLMResponse

_DEFAULT_LOG_PATH = Path("logs/llm_usage.jsonl")


class CostLogger:
    def __init__(self, log_path: Path | None = None) -> None:
        self._log_path = log_path or _DEFAULT_LOG_PATH
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        response: LLMResponse,
        *,
        tenant_id: str | None = None,
        request_tag: str | None = None,
        prompt_full_name: str | None = None,
    ) -> None:
        """Appends one line to the file. Never raises if the write
        fails (see _write) -- losing a cost record must not take
        down a call that already succeeded."""
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "provider": response.provider,
            "model": response.model,
            "input_tokens": response.tokens_used.input_tokens,
            "output_tokens": response.tokens_used.output_tokens,
            "total_tokens": response.tokens_used.total_tokens,
            "estimated_cost_usd": response.estimated_cost_usd,
            "latency_ms": response.latency_ms,
            "finish_reason": response.finish_reason,
            "tenant_id": tenant_id,
            "request_tag": request_tag,
            "prompt_full_name": prompt_full_name,
        }
        self._write(record)

    def _write(self, record: dict) -> None:
        try:
            with self._log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            # Cost logging is best-effort: a full disk or a denied
            # permission must not break the response to the user.
            pass