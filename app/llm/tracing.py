"""
LangfuseTracer: wraps the Langfuse SDK to record one "generation"
observation per real call to an LLM provider.

Lives as its own optional dependency injected into LLMClient, same
pattern as CostLogger -- if no tracer is passed, nothing changes.
Unlike CostLogger (which persists cost data locally as ground truth),
Langfuse is for visual debugging and prompt-level observability: full
input/output per call, grouped into traces, browsable in a dashboard.
"""

from __future__ import annotations

from typing import Any

from langfuse import Langfuse
from langfuse._client.span import LangfuseGeneration

from app.llm.schemas import LLMRequest, LLMResponse


class LangfuseTracer:
    def __init__(self, client: Langfuse | None = None) -> None:
        # Langfuse() with no args reads LANGFUSE_PUBLIC_KEY /
        # LANGFUSE_SECRET_KEY / LANGFUSE_HOST from the environment.
        self._client = client or Langfuse()

    def start_generation(
        self, request: LLMRequest, provider_name: str
    ) -> LangfuseGeneration:
        """Opens one generation observation for a single real call to
        the provider. Must be paired with end_generation_success or
        end_generation_error once the call resolves."""
        return self._client.start_observation(
            name=request.request_tag or "llm-generation",
            as_type="generation",
            input=[message.model_dump(mode="json") for message in request.messages],
            model=request.model,
            model_parameters={"temperature": request.temperature},
            metadata=self._build_metadata(request, provider_name),
        )

    def end_generation_success(
        self, generation: LangfuseGeneration, response: LLMResponse
    ) -> None:
        generation.update(
            output=response.content,
            usage_details={
                "input": response.tokens_used.input_tokens,
                "output": response.tokens_used.output_tokens,
                "total": response.tokens_used.total_tokens,
            },
            cost_details=(
                {"total": response.estimated_cost_usd}
                if response.estimated_cost_usd is not None
                else None
            ),
        )
        generation.end()

    def end_generation_error(
        self, generation: LangfuseGeneration, error: Exception
    ) -> None:
        generation.update(level="ERROR", status_message=str(error))
        generation.end()

    def flush(self) -> None:
        """Forces any buffered observations to be sent immediately.
        Langfuse batches by default -- call this in short-lived
        scripts (tests, one-off runs) so nothing is lost on exit."""
        self._client.flush()

    @staticmethod
    def _build_metadata(request: LLMRequest, provider_name: str) -> dict[str, Any]:
        return {
            "provider": provider_name,
            "tenant_id": request.tenant_id,
        }