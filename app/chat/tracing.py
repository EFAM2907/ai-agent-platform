"""Traza de un turno de chat.

Un turno toca varios sistemas -- el clasificador de modelo, la busqueda
en la KB, una o mas vueltas del LLM, herramientas MCP que a su vez
llaman a la API -- y hasta ahora no quedaba constancia de que paso en
cada uno. TurnTracer junta todo eso mientras el turno ocurre y lo deja
listo para guardarse como una fila de chat_turn_traces.

Implementa el protocolo ToolLoopObserver de app.llm.tools, asi el loop
le avisa de cada llamada al modelo y de cada herramienta sin saber nada
de trazabilidad.

Privacidad: de las herramientas se guardan el nombre, los ARGUMENTOS
(con los campos sensibles ocultos) y el TAMANO del resultado -- no el
resultado en si, que puede traer datos personales de clientes o
usuarios. Del contenido de la KB solo se guardan ids, titulos y scores.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.chat.models import ChatTurnTrace
from app.llm.schemas import LLMResponse

# Argumentos que nunca se guardan en claro, por nombre (en cualquier nivel).
_SENSITIVE_KEYS = ("password", "passwd", "secret", "token", "api_key", "authorization")
_MAX_ARG_CHARS = 500
_MAX_ERROR_CHARS = 500


def redact_arguments(value: Any) -> Any:
    """Copia de los argumentos de una tool sin secretos y con textos largos
    recortados -- para guardarlos en la traza sin riesgo."""
    if isinstance(value, dict):
        return {
            key: "***" if any(s in str(key).lower() for s in _SENSITIVE_KEYS) else redact_arguments(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_arguments(item) for item in value[:20]]
    if isinstance(value, str) and len(value) > _MAX_ARG_CHARS:
        return value[:_MAX_ARG_CHARS] + "…"
    return value


@dataclass
class _LLMCall:
    purpose: str  # "router" | "answer"
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    cost_usd: float | None
    finish_reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "purpose": self.purpose,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_ms": round(self.latency_ms, 1),
            "cost_usd": self.cost_usd,
            "finish_reason": self.finish_reason,
        }


class TurnTracer:
    def __init__(self) -> None:
        # uuid4().hex = 32 caracteres hex: formato de trace id que Langfuse acepta.
        self.trace_id = uuid.uuid4().hex
        self._started_at = time.perf_counter()
        self.route: dict[str, Any] = {}
        self.retrieval: dict[str, Any] = {}
        self.llm_calls: list[_LLMCall] = []
        self.tool_calls: list[dict[str, Any]] = []
        self.model: str | None = None
        self.delegation: dict[str, Any] = {}

    # -- registro ------------------------------------------------------
    def record_route(self, model: str, reason: str, classifier: LLMResponse | None) -> None:
        self.model = model
        self.route = {"model": model, "reason": reason}
        if classifier is not None:
            self._add_llm_call("router", classifier)

    def record_delegation(self, claims: dict[str, Any]) -> None:
        """Actor (quien llama) y sujeto (en nombre de quien) del token
        delegado de este turno -- nunca el token en si."""
        self.delegation = {
            "actor": (claims.get("act") or {}).get("sub"),
            "sub": claims.get("sub"),
            "jti": claims.get("jti"),
            "aud": claims.get("aud"),
            "scopes": sorted(str(claims.get("scope", "")).split()),
        }

    def record_retrieval(
        self,
        *,
        mode: str,
        results: list[tuple[Any, str, float]],
        latency_ms: float,
        error: str | None = None,
    ) -> None:
        self.retrieval = {
            "mode": mode,
            "returned": len(results),
            "latency_ms": round(latency_ms, 1),
            "chunks": [
                {
                    "chunk_id": str(chunk.id),
                    "article_id": str(chunk.article_id),
                    "title": title,
                    "score": round(float(score), 5),
                }
                for chunk, title, score in results
            ],
        }
        if error:
            # La busqueda fallo y el turno siguio sin contexto de la KB.
            self.retrieval["error"] = error[:_MAX_ERROR_CHARS]

    def _add_llm_call(self, purpose: str, response: LLMResponse) -> None:
        self.llm_calls.append(
            _LLMCall(
                purpose=purpose,
                model=response.model,
                input_tokens=response.tokens_used.input_tokens,
                output_tokens=response.tokens_used.output_tokens,
                latency_ms=response.latency_ms,
                cost_usd=response.estimated_cost_usd,
                finish_reason=response.finish_reason,
            )
        )

    # -- ToolLoopObserver ------------------------------------------------
    def on_llm_response(self, response: LLMResponse) -> None:
        self._add_llm_call("answer", response)
        # El modelo que REALMENTE respondio puede diferir del pedido (fallback del gateway).
        self.model = response.model or self.model

    def on_tool_call(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        ok: bool,
        duration_ms: float,
        error: str | None,
        result_chars: int,
    ) -> None:
        entry: dict[str, Any] = {
            "name": name,
            "arguments": redact_arguments(arguments),
            "ok": ok,
            "duration_ms": round(duration_ms, 1),
            "result_chars": result_chars,
        }
        if error:
            entry["error"] = error[:_MAX_ERROR_CHARS]
        self.tool_calls.append(entry)

    # -- salida ------------------------------------------------------------
    @property
    def elapsed_ms(self) -> int:
        return int((time.perf_counter() - self._started_at) * 1000)

    def summary(self) -> dict[str, Any]:
        """Lo que el frontend recibe en el evento 'done' (sin chunks ni argumentos)."""
        return {
            "trace_id": self.trace_id,
            "model": self.model,
            "latency_ms": self.elapsed_ms,
            "tools": [{"name": t["name"], "ok": t["ok"]} for t in self.tool_calls],
        }

    def build(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        user_message_id: uuid.UUID | None,
        assistant_message_id: uuid.UUID | None,
        error: BaseException | None = None,
    ) -> ChatTurnTrace:
        costs = [c.cost_usd for c in self.llm_calls if c.cost_usd is not None]
        return ChatTurnTrace(
            organization_id=organization_id,
            user_id=user_id,
            session_id=session_id,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            trace_id=self.trace_id,
            status="error" if error else "ok",
            error_type=type(error).__name__ if error else None,
            error_message=str(error)[:_MAX_ERROR_CHARS] if error else None,
            model=self.model,
            route=self.route,
            retrieval=self.retrieval,
            llm_calls=[c.as_dict() for c in self.llm_calls],
            tool_calls=self.tool_calls,
            delegation=self.delegation,
            input_tokens=sum(c.input_tokens for c in self.llm_calls),
            output_tokens=sum(c.output_tokens for c in self.llm_calls),
            cost_usd=Decimal(str(round(sum(costs), 6))) if costs else None,
            latency_ms=self.elapsed_ms,
        )
