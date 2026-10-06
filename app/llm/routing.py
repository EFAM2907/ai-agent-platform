"""
ModelRouter: decides which model tier should handle a request, using
a cheap classification call for anything that could plausibly need
judgment -- not intuition, not a static rule based on message length
or keywords.

The classification itself always runs on the cheap model, regardless
of which tier ends up handling the real task -- so the cost of
"deciding" stays low even for requests that get routed to the
expensive tier.

The actual accuracy of this classifier against real incidents is
NOT assumed here -- see eval_harness/model_routing_eval.py, which
measures it against a labeled dataset instead of trusting this
docstring.

The one exception is `_is_obviously_trivial`: a narrow, structural
(not semantic) check for messages that plainly carry no request at
all -- a bare greeting, "gracias", "ok" -- where classification isn't
skipped because we're guessing the answer is "cheap", but because
there's nothing there FOR the classifier to classify. It never
substitutes for the classifier's judgment on anything with actual
content; every eval_harness case and every real support question
still goes through the real LLM call.
"""

from __future__ import annotations

from app.llm.client import LLMClient
from app.llm.errors import LLMError
from app.llm.prompts.loader import PromptLoader
from app.llm.schemas import LLMRequest, Message, Role

_CLASSIFIER_PROMPT_NAME = "route_model_complexity"
_CLASSIFIER_PROMPT_VERSION = 1

# Un mensaje mas corto que esto no puede contener un pedido real (ni
# siquiera "borra a Juan" entra) -- es estructural, no una apuesta
# sobre el contenido. Las frases son saludos/cierres frecuentes que sí
# superan ese largo (ej. "buenas tardes", "muchas gracias").
_TRIVIAL_MAX_LENGTH = 12
_TRIVIAL_PHRASES = frozenset({
    "hola", "hi", "hello", "hey", "buenas", "buenos dias", "buenas tardes",
    "buenas noches", "gracias", "muchas gracias", "thanks", "thank you",
    "ok", "okay", "vale", "adios", "bye", "chau", "listo", "perfecto",
})


class ModelRouter:
    def __init__(
        self,
        llm_client: LLMClient,
        *,
        cheap_model: str,
        expensive_model: str,
        prompt_loader: PromptLoader | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._cheap_model = cheap_model
        self._expensive_model = expensive_model
        self._prompt_loader = prompt_loader or PromptLoader()
        # Versión fija, no load_latest(): el classifier del eval harness
        # compara accuracy contra un dataset etiquetado a mano -- si el
        # prompt cambiara solo, esos resultados dejarían de ser
        # comparables entre corridas sin que nadie lo notara.
        self._classifier_prompt = self._prompt_loader.load_version(
            _CLASSIFIER_PROMPT_NAME, _CLASSIFIER_PROMPT_VERSION
        )

    async def choose_model(self, user_message: str) -> str:
        """Returns cheap_model or expensive_model -- never raises on
        classifier failure; if the classification call itself fails
        or comes back malformed, defaults to the expensive model.
        Being wrong toward "too capable" is a cost problem; being
        wrong toward "too simple" is a correctness/safety problem for
        a support agent that might execute real actions -- so the
        failure mode leans safe, not cheap.

        Skips the classification call entirely for `_is_obviously_trivial`
        messages -- see the module docstring for why that is not the
        same thing as guessing from keywords."""
        if self._is_obviously_trivial(user_message):
            return self._cheap_model

        request = LLMRequest(
            messages=[
                Message(
                    role=Role.SYSTEM, content=self._classifier_prompt.system_prompt
                ),
                Message(role=Role.USER, content=user_message),
            ],
            model=self._cheap_model,
            response_schema=self._classifier_prompt.response_schema,
        )

        try:
            response = await self._llm_client.generate(request)
            needs_expensive = response.parsed["needs_expensive_model"]
        except (LLMError, KeyError, TypeError):
            # LLMError: el provider/gateway falló de verdad.
            # KeyError/TypeError: response.parsed vino sin la llave
            # esperada (no debería pasar si el loop de reparación de
            # LLMClient hizo su trabajo, pero no confiamos a ciegas).
            # Cualquier otra excepción (un bug real en este método) se
            # propaga sin disfrazarse -- no la escondemos como si
            # fuera un fallo esperado del clasificador.
            return self._expensive_model

        return self._expensive_model if needs_expensive else self._cheap_model

    @staticmethod
    def _is_obviously_trivial(user_message: str) -> bool:
        normalized = user_message.strip().lower().rstrip("!?.,¡¿")
        if not normalized:
            return True
        if len(normalized) <= _TRIVIAL_MAX_LENGTH:
            return True
        return normalized in _TRIVIAL_PHRASES
