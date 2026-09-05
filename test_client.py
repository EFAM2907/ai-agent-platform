"""
Tests de LLMClient: retries, backoff+jitter, y qué errores NO se
reintentan.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.llm.client import LLMClient
from app.llm.errors import ContentFilterError, InvalidResponseError, ProviderError, RateLimitError
from app.llm.schemas import LLMRequest, LLMResponse, Message, Role, TokenUsage


@pytest.fixture
def request_() -> LLMRequest:
    return LLMRequest(
        messages=[Message(role=Role.USER, content="hola")], model="gpt-4o-mini"
    )


def _fake_response() -> LLMResponse:
    return LLMResponse(
        content="ok",
        model="gpt-4o-mini",
        provider="fake",
        tokens_used=TokenUsage(input_tokens=1, output_tokens=1),
        latency_ms=10.0,
        finish_reason="stop",
    )


class TestRetries:
    @pytest.mark.asyncio
    async def test_reintenta_y_eventualmente_tiene_exito(self, request_):
        provider = AsyncMock()
        provider.generate.side_effect = [
            ProviderError("falla 1", provider="fake"),
            ProviderError("falla 2", provider="fake"),
            _fake_response(),
        ]
        client = LLMClient(provider, max_retries=3, base_delay_seconds=0.01)

        result = await client.generate(request_)

        assert result.content == "ok"
        assert provider.generate.call_count == 3

    @pytest.mark.asyncio
    async def test_agota_reintentos_y_relanza_el_ultimo_error(self, request_):
        provider = AsyncMock()
        provider.generate.side_effect = ProviderError("siempre falla", provider="fake")
        client = LLMClient(provider, max_retries=2, base_delay_seconds=0.01)

        with pytest.raises(ProviderError, match="siempre falla"):
            await client.generate(request_)

        # 1 intento inicial + 2 reintentos = 3 llamadas totales
        assert provider.generate.call_count == 3

    @pytest.mark.asyncio
    async def test_content_filter_no_se_reintenta(self, request_):
        provider = AsyncMock()
        provider.generate.side_effect = ContentFilterError(
            "bloqueado", provider="fake"
        )
        client = LLMClient(provider, max_retries=3, base_delay_seconds=0.01)

        with pytest.raises(ContentFilterError):
            await client.generate(request_)

        # No debe reintentar algo que nunca se va a arreglar solo.
        assert provider.generate.call_count == 1

    @pytest.mark.asyncio
    async def test_respeta_retry_after_de_rate_limit(self, request_, monkeypatch):
        provider = AsyncMock()
        provider.generate.side_effect = [
            RateLimitError("limite", provider="fake", retry_after=0.05),
            _fake_response(),
        ]
        client = LLMClient(provider, max_retries=2, base_delay_seconds=5.0)

        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        monkeypatch.setattr("app.llm.client.asyncio.sleep", fake_sleep)

        await client.generate(request_)

        # Debe usar el retry_after del error (0.05), no el backoff
        # exponencial de base_delay_seconds=5.0.
        assert sleeps == [0.05]


class TestStructuredOutputRepair:
    @staticmethod
    def _schema() -> dict:
        return {
            "type": "object",
            "properties": {
                "ruta": {"type": "string", "enum": ["kb", "datos", "accion"]},
                "confianza": {"type": "number"},
            },
            "required": ["ruta", "confianza"],
        }

    @staticmethod
    def _structured_request() -> LLMRequest:
        return LLMRequest(
            messages=[Message(role=Role.USER, content="no puedo entrar")],
            model="gpt-4o-mini",
            response_schema=TestStructuredOutputRepair._schema(),
        )

    @staticmethod
    def _response_with(content: str) -> LLMResponse:
        return LLMResponse(
            content=content,
            model="gpt-4o-mini",
            provider="fake",
            tokens_used=TokenUsage(input_tokens=1, output_tokens=1),
            latency_ms=10.0,
            finish_reason="stop",
        )

    @pytest.mark.asyncio
    async def test_json_valido_al_primer_intento_no_repara(self):
        provider = AsyncMock()
        provider.generate.return_value = self._response_with(
            '{"ruta": "kb", "confianza": 0.9}'
        )
        client = LLMClient(provider, base_delay_seconds=0.01)

        result = await client.generate(self._structured_request())

        assert result.parsed == {"ruta": "kb", "confianza": 0.9}
        assert provider.generate.call_count == 1

    @pytest.mark.asyncio
    async def test_repara_tras_json_roto_en_primer_intento(self):
        provider = AsyncMock()
        provider.generate.side_effect = [
            self._response_with("esto no es json"),
            self._response_with('{"ruta": "accion", "confianza": 0.75}'),
        ]
        client = LLMClient(
            provider, base_delay_seconds=0.01, max_structured_repair_attempts=2
        )

        result = await client.generate(self._structured_request())

        assert result.parsed == {"ruta": "accion", "confianza": 0.75}
        assert provider.generate.call_count == 2

        # El segundo llamado debe incluir la respuesta rota y el error
        # como contexto para que el modelo se corrija.
        segunda_request: LLMRequest = provider.generate.call_args_list[1].args[0]
        contenidos = [m.content for m in segunda_request.messages]
        assert "esto no es json" in contenidos
        assert any("JSON válido" in c for c in contenidos)

    @pytest.mark.asyncio
    async def test_repara_cuando_json_es_valido_pero_no_cumple_schema(self):
        provider = AsyncMock()
        provider.generate.side_effect = [
            # JSON válido, pero "ruta" no está en el enum permitido.
            self._response_with('{"ruta": "otra_cosa", "confianza": 0.5}'),
            self._response_with('{"ruta": "kb", "confianza": 0.5}'),
        ]
        client = LLMClient(provider, base_delay_seconds=0.01)

        result = await client.generate(self._structured_request())

        assert result.parsed["ruta"] == "kb"
        assert provider.generate.call_count == 2

    @pytest.mark.asyncio
    async def test_agota_intentos_de_reparacion_y_lanza_invalid_response(self):
        provider = AsyncMock()
        provider.generate.return_value = self._response_with("siempre roto")
        client = LLMClient(
            provider, base_delay_seconds=0.01, max_structured_repair_attempts=2
        )

        with pytest.raises(InvalidResponseError):
            await client.generate(self._structured_request())

        # 1 intento inicial + 2 reparaciones = 3 llamadas al provider.
        assert provider.generate.call_count == 3


class TestComplete:
    @pytest.mark.asyncio
    async def test_complete_arma_llmrequest_correctamente(self):
        provider = AsyncMock()
        provider.generate.return_value = TestStructuredOutputRepair._response_with("hola")
        client = LLMClient(provider, base_delay_seconds=0.01)

        result = await client.complete(
            "hola",
            system_prompt="eres un asistente de soporte",
            model="gpt-4o-mini",
        )

        assert result.content == "hola"
        enviado: LLMRequest = provider.generate.call_args.args[0]
        assert enviado.model == "gpt-4o-mini"
        assert enviado.messages[0].role == Role.SYSTEM
        assert enviado.messages[1].role == Role.USER
        assert enviado.messages[1].content == "hola"


class TestCostLoggingAutomatico:
    @pytest.mark.asyncio
    async def test_loguea_automaticamente_tras_generate_exitoso(self, tmp_path):
        from app.llm.cost_logger import CostLogger

        log_path = tmp_path / "usage.jsonl"
        cost_logger = CostLogger(log_path=log_path)

        provider = AsyncMock()
        provider.generate.return_value = _fake_response()
        client = LLMClient(provider, cost_logger=cost_logger)

        request = LLMRequest(
            messages=[Message(role=Role.USER, content="hola")],
            model="gpt-4o-mini",
            tenant_id="empresa-acme",
            request_tag="saludo",
        )
        await client.generate(request)

        lineas = log_path.read_text().strip().split("\n")
        assert len(lineas) == 1

        import json
        record = json.loads(lineas[0])
        assert record["tenant_id"] == "empresa-acme"
        assert record["request_tag"] == "saludo"

    @pytest.mark.asyncio
    async def test_no_loguea_nada_si_no_se_inyecta_cost_logger(self):
        provider = AsyncMock()
        provider.generate.return_value = _fake_response()
        client = LLMClient(provider)  # sin cost_logger

        # No debe lanzar ni requerir nada -- simplemente no loguea.
        result = await client.generate(
            LLMRequest(
                messages=[Message(role=Role.USER, content="hola")],
                model="gpt-4o-mini",
            )
        )
        assert result.content == "ok"

    @pytest.mark.asyncio
    async def test_loguea_cada_intento_del_loop_de_reparacion(self, tmp_path):
        from app.llm.cost_logger import CostLogger

        log_path = tmp_path / "usage.jsonl"
        cost_logger = CostLogger(log_path=log_path)

        provider = AsyncMock()
        provider.generate.side_effect = [
            TestStructuredOutputRepair._response_with("json roto"),
            TestStructuredOutputRepair._response_with('{"ruta": "kb", "confianza": 0.9}'),
        ]
        client = LLMClient(provider, base_delay_seconds=0.01, cost_logger=cost_logger)

        await client.generate(TestStructuredOutputRepair._structured_request())

        lineas = log_path.read_text().strip().split("\n")
        # 2 llamadas reales al provider = 2 líneas logueadas, aunque
        # la primera haya sido JSON roto -- también costó tokens.
        assert len(lineas) == 2


class TestLangfuseTracingIntegration:
    @pytest.mark.asyncio
    async def test_traces_a_successful_call(self):
        from unittest.mock import MagicMock

        from app.llm.tracing import LangfuseTracer

        mock_langfuse_client = MagicMock()
        mock_generation = MagicMock()
        mock_langfuse_client.start_observation.return_value = mock_generation
        tracer = LangfuseTracer(client=mock_langfuse_client)

        provider = AsyncMock()
        provider.name = "gemini"
        provider.generate.return_value = _fake_response()
        client = LLMClient(provider, tracer=tracer)

        await client.generate(
            LLMRequest(
                messages=[Message(role=Role.USER, content="hola")],
                model="gemini-3.6-flash",
            )
        )

        mock_langfuse_client.start_observation.assert_called_once()
        mock_generation.update.assert_called_once()
        mock_generation.end.assert_called_once()

    @pytest.mark.asyncio
    async def test_traces_each_retry_attempt_as_a_separate_observation(self):
        from unittest.mock import MagicMock

        from app.llm.tracing import LangfuseTracer

        mock_langfuse_client = MagicMock()
        tracer = LangfuseTracer(client=mock_langfuse_client)

        provider = AsyncMock()
        provider.name = "gemini"
        provider.generate.side_effect = [
            ProviderError("temporary failure", provider="gemini"),
            _fake_response(),
        ]
        client = LLMClient(provider, tracer=tracer, base_delay_seconds=0.01)

        await client.generate(
            LLMRequest(
                messages=[Message(role=Role.USER, content="hola")],
                model="gemini-3.6-flash",
            )
        )

        # 2 real calls to the provider = 2 separate observations opened.
        assert mock_langfuse_client.start_observation.call_count == 2

    @pytest.mark.asyncio
    async def test_no_tracing_happens_if_no_tracer_is_injected(self):
        provider = AsyncMock()
        provider.generate.return_value = _fake_response()
        client = LLMClient(provider)  # no tracer

        # Must not raise, must behave exactly as before tracing existed.
        result = await client.generate(
            LLMRequest(
                messages=[Message(role=Role.USER, content="hola")],
                model="gemini-3.6-flash",
            )
        )
        assert result.content == "ok"