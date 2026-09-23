"""Extraccion de numeros de guia desde el texto de un correo.

Solo propone CANDIDATOS: un numero cualquiera de 6-15 digitos puede ser
un telefono, una cedula o un monto. Quien decide cual es una guia real
es la base de datos (el procesador consulta cada candidato contra los
envios de la organizacion) -- por eso aca no hace falta ser exacto,
solo no perder guias reales ni devolver una lista enorme.

Un candidato "explicito" es el que viene pegado a una palabra clave
("guia 854321", "tracking: 854321"). Se usa para decidir si vale la pena
contestar "no encontramos esa guia" cuando no existe en la base: sin
palabra clave, un numero suelto en un correo cualquiera no justifica
responderle a nadie.

Formatos de guia:
  - Tras una palabra clave se acepta tambien un codigo con letras y
    guiones ("IR-2024-00123", "IR123456CO"): ahi hay una senal fuerte de
    que lo que sigue ES una guia.
  - Un numero SUELTO (sin palabra clave) solo se propone si calza con
    settings.gmail_tracking_bare_pattern (por defecto, 6-15 digitos).
    Si las guias de la organizacion tienen otro formato, se ajusta esa
    variable en vez de tocar el codigo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from app.core.config import settings

MAX_CANDIDATES = 5

# Un candidato no debe ser parte de una palabra, direccion de correo o
# URL (user123456@x.com, /pedido/123456/).
_BEFORE = r"(?<![\w@/])"
_AFTER = r"(?![\w@/])"

# Codigo con letras/guiones/digitos: 5-30 caracteres, empieza y termina
# en alfanumerico y trae al menos 4 digitos (asi "guia es" o "guia a2"
# no cuentan). Solo se usa pegado a una palabra clave.
_KEYWORD_CODE = (
    _BEFORE
    + r"(?=(?:[A-Za-z-]*\d){4})[A-Za-z0-9][A-Za-z0-9-]{3,28}[A-Za-z0-9]"
    + _AFTER
)

# "envio" a secas NO es palabra clave (aparece en cualquier correo de una
# mensajeria: "hacen envios a Cali?"); solo "numero de envio". Y el
# numero tiene que estar en la misma linea que la palabra clave.
_KEYWORD_NUMBER = re.compile(
    r"(?:gu[ií]a|rastreo|tracking|seguimiento|(?:n[uú]mero|n[°º]|nro\.?)\s+de\s+env[ií]o)"
    r"[^\d\n]{0,25}?(" + _KEYWORD_CODE + r")",
    re.IGNORECASE,
)


@lru_cache(maxsize=8)
def _compile_bare(pattern: str) -> re.Pattern[str]:
    return re.compile(_BEFORE + r"(?:" + pattern + r")" + _AFTER)


@dataclass(frozen=True)
class TrackingCandidate:
    number: str
    explicit: bool


def _unique(items) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def extract_tracking_candidates(text: str) -> list[TrackingCandidate]:
    """Candidatos unicos en orden de aparicion, los explicitos primero."""
    explicit = _unique(m.group(1) for m in _KEYWORD_NUMBER.finditer(text))
    bare = _unique(
        m.group(0)
        for m in _compile_bare(settings.gmail_tracking_bare_pattern).finditer(text)
        # Un numero que ya es parte de un codigo explicito no es otro.
        if not any(m.group(0) in code for code in explicit)
    )

    candidates = [TrackingCandidate(number=n, explicit=True) for n in explicit]
    candidates += [TrackingCandidate(number=n, explicit=False) for n in bare if n not in explicit]
    return candidates[:MAX_CANDIDATES]


# --- Intencion: ¿el correo pregunta por el estado de un envio? -----------
#
# Se usa SOLO cuando el correo no trae ninguna guia, para decidir si vale
# la pena pedirsela al cliente. Es deliberadamente conservador y
# determinista (sin LLM: el correo es entrada no confiable): preguntar
# "¿hacen envios a Cali?" o "quiero hacer un envio" NO cuenta, porque
# pedirle una guia a quien no tiene ninguna seria una respuesta absurda.
# Pecar por omision es barato: el correo queda sin responder, como antes.
_SHIPMENT_NOUN = r"(?:env[ií]o|paquete|pedido|encomienda|gu[ií]a|giro|mercanc[ií]a|carga)s?"
_STATUS_INQUIRY = re.compile(
    r"\b(?:"
    r"rastre(?:o|ar|arlo|arla)"
    r"|seguimiento"
    r"|tracking"
    r"|d[oó]nde\s+(?:est[aá]|va|viene|queda|anda)"
    r"|c[oó]mo\s+va"
    r"|estado\s+(?:de|del|actual)"
    r"|(?:no\s+)?(?:ha\s+|han\s+)?llegado"
    r"|sigue\s+sin\s+llegar"
    r"|cu[aá]ndo\s+(?:llega|llegar[aá])"
    r"|retras(?:o|ado|ada)"
    r"|(?:mi|mis|la|su|n[uú]mero\s+de)\s+gu[ií]a"
    r"|(?:saber|consultar|consulta|averiguar|preguntar)\s+"
    r"(?:de|del|por|sobre|acerca\s+de)\s+(?:mi|mis|el|la)\s+" + _SHIPMENT_NOUN +
    r")\b",
    re.IGNORECASE,
)


def looks_like_status_inquiry(text: str) -> bool:
    return _STATUS_INQUIRY.search(text) is not None
