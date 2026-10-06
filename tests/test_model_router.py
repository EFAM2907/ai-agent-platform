from unittest.mock import AsyncMock

import pytest

from app.llm.errors import ProviderError
from app.llm.routing import ModelRouter
from app.llm.schemas import LLMResponse, TokenUsage

CHEAP_MODEL = "gemini-3.6-flash"
EXPENSIVE_MODEL = "claude-sonnet-5"


def _classification_response(needs_expensive: bool) -> LLMResponse:
    return LLMResponse(
        content='{"needs_expensive_model": %s, "reasoning": "porque si"}'
        % str(needs_expensive).lower(),
        model=CHEAP_MODEL,
        provider="litellm",
        tokens_used=TokenUsage(input_tokens=10, output_tokens=5),
        latency_ms=5.0,
        finish_reason="stop",
        parsed={"needs_expensive_model": needs_expensive, "reasoning": "porque si"},
    )


def _router(llm_client) -> ModelRouter:
    return ModelRouter(
        llm_client, cheap_model=CHEAP_MODEL, expensive_model=EXPENSIVE_MODEL
    )


@pytest.mark.asyncio
async def test_returns_cheap_model_when_classifier_says_not_expensive():
    llm_client = AsyncMock()
    llm_client.generate.return_value = _classification_response(False)

    result = await _router(llm_client).choose_model("¿Cómo reseteo mi contraseña?")

    assert result == CHEAP_MODEL


@pytest.mark.asyncio
async def test_returns_expensive_model_when_classifier_says_expensive():
    llm_client = AsyncMock()
    llm_client.generate.return_value = _classification_response(True)

    result = await _router(llm_client).choose_model(
        "Desactiven estos 5 usuarios y reasignen sus recursos a Marta"
    )

    assert result == EXPENSIVE_MODEL


@pytest.mark.asyncio
async def test_classification_call_always_targets_cheap_model_regardless_of_result():
    for needs_expensive in (True, False):
        llm_client = AsyncMock()
        llm_client.generate.return_value = _classification_response(needs_expensive)

        await _router(llm_client).choose_model("cualquier consulta")

        sent_request = llm_client.generate.call_args.args[0]
        assert sent_request.model == CHEAP_MODEL


@pytest.mark.asyncio
async def test_falls_back_to_expensive_model_when_llm_client_raises():
    llm_client = AsyncMock()
    llm_client.generate.side_effect = ProviderError("gateway caido", provider="litellm")

    result = await _router(llm_client).choose_model("cualquier consulta")

    assert result == EXPENSIVE_MODEL


@pytest.mark.asyncio
async def test_falls_back_to_expensive_model_when_parsed_is_missing_expected_key():
    llm_client = AsyncMock()
    malformed = _classification_response(False)
    malformed.parsed = {"reasoning": "sin la key needs_expensive_model"}
    llm_client.generate.return_value = malformed

    result = await _router(llm_client).choose_model("cualquier consulta")

    assert result == EXPENSIVE_MODEL


@pytest.mark.parametrize("trivial_message", [
    "hola",
    "Hola!",
    "gracias",
    "Muchas gracias!",
    "ok",
    "  ",
    "",
    "buenas tardes",
])
@pytest.mark.asyncio
async def test_obviously_trivial_messages_skip_the_classifier_call(trivial_message):
    llm_client = AsyncMock()

    result = await _router(llm_client).choose_model(trivial_message)

    assert result == CHEAP_MODEL
    llm_client.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_short_but_actionable_message_still_calls_the_classifier():
    """Mas de 12 caracteres ya alcanza para un pedido real ("elimina a
    Juan" son 14) -- el heuristico no debe tragarse eso, tiene que
    llegar al clasificador real."""
    llm_client = AsyncMock()
    llm_client.generate.return_value = _classification_response(True)

    result = await _router(llm_client).choose_model("elimina a Juan")

    assert result == EXPENSIVE_MODEL
    llm_client.generate.assert_awaited_once()
