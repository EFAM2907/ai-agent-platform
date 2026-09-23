"""Convierte un mensaje crudo de la Gmail API en un ParsedEmail limpio.

Todo lo que sale de aca es entrada NO confiable (la escribe un tercero):
se acota en tamano, se le quitan saltos de linea de las cabeceras (para
no permitir inyeccion de cabeceras al construir la respuesta) y se
descartan las citas de mensajes anteriores -- si no, un "gracias" del
cliente que cita nuestra respuesta volveria a disparar otra respuesta
con la misma guia.
"""

from __future__ import annotations

import base64
import html
import re
from dataclasses import dataclass
from email.utils import parseaddr
from typing import Any

MAX_BODY_CHARS = 20_000
MAX_HEADER_CHARS = 500

_QUOTE_INTRO = re.compile(
    r"^\s*(?:El .{5,200}escribi[oó]:?|On .{5,200}wrote:?|-{2,}\s*(?:Original Message|Mensaje original).*)\s*$",
    re.IGNORECASE,
)
_BLOCKQUOTE = re.compile(r"<blockquote\b.*?</blockquote>", re.IGNORECASE | re.DOTALL)
_SCRIPT_STYLE = re.compile(r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
_BR_OR_BLOCK_END = re.compile(r"<\s*(?:br\s*/?|/p|/div|/tr|/li)\s*>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")

# Remitentes que nunca deben recibir una respuesta automatica.
_AUTOMATED_LOCALPARTS = re.compile(
    r"^(?:no[-_.]?reply|do[-_.]?not[-_.]?reply|mailer-daemon|postmaster|bounce[s]?|notifications?)(?:[+._-].*)?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedEmail:
    message_id: str
    thread_id: str | None
    from_address: str
    from_name: str
    subject: str
    # Cabecera Message-ID original (con < >), para In-Reply-To/References.
    rfc_message_id: str | None
    references: str | None
    body: str
    is_automated: bool
    # Gmail aprobo SPF/DKIM/DMARC para el dominio del From: el remitente
    # es quien dice ser (sin esto, el From se falsifica con una linea).
    sender_authenticated: bool = False


def _clean_header(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[\r\n\x00]+", " ", value).strip()[:MAX_HEADER_CHARS]


def _decode_part(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return ""


def _collect_parts(payload: dict[str, Any], plain: list[str], htmls: list[str]) -> None:
    mime = payload.get("mimeType", "")
    data = (payload.get("body") or {}).get("data")
    if data and mime == "text/plain":
        plain.append(_decode_part(data))
    elif data and mime == "text/html":
        htmls.append(_decode_part(data))
    for child in payload.get("parts") or []:
        _collect_parts(child, plain, htmls)


def _html_to_text(raw_html: str) -> str:
    text = _BLOCKQUOTE.sub("", raw_html)
    text = _SCRIPT_STYLE.sub("", text)
    text = _BR_OR_BLOCK_END.sub("\n", text)
    text = _TAG.sub("", text)
    return html.unescape(text)


def _strip_quoted(text: str) -> str:
    kept: list[str] = []
    for line in text.splitlines():
        if _QUOTE_INTRO.match(line):
            break  # todo lo que sigue es el mensaje citado
        if line.lstrip().startswith(">"):
            continue
        kept.append(line)
    return "\n".join(kept)


def extract_body(payload: dict[str, Any]) -> str:
    plain: list[str] = []
    htmls: list[str] = []
    _collect_parts(payload, plain, htmls)
    if plain:
        text = "\n".join(plain)
    else:
        text = "\n".join(_html_to_text(h) for h in htmls)
    return _strip_quoted(text)[:MAX_BODY_CHARS].strip()


def _is_automated(headers: dict[str, str], address: str) -> bool:
    auto_submitted = headers.get("auto-submitted", "no").strip().lower()
    if auto_submitted != "no":
        return True
    if headers.get("precedence", "").strip().lower() in {"bulk", "list", "junk", "auto_reply"}:
        return True
    if "list-id" in headers or "list-unsubscribe" in headers:
        return True
    if "x-autoreply" in headers or "x-autorespond" in headers:
        return True
    local = address.split("@", 1)[0]
    return bool(_AUTOMATED_LOCALPARTS.match(local))


def _trusted_auth_results(raw_headers: list[dict[str, Any]]) -> str | None:
    """Cabecera Authentication-Results agregada por Gmail (authserv-id
    mx.google.com). Gmail la antepone al mensaje, asi que la PRIMERA es la
    suya; cualquier otra mas abajo la puso quien envio el correo y no
    vale nada -- por eso no sirve el dict de cabeceras, donde la ultima
    pisa a la primera."""
    for header in raw_headers:
        if str(header.get("name", "")).lower() != "authentication-results":
            continue
        value = str(header.get("value", ""))
        return value if value.strip().lower().startswith("mx.google.com") else None
    return None


def _aligned(domain: str, sender_domain: str) -> bool:
    """Alineacion "relajada" de DMARC: mismo dominio, o uno es subdominio
    del otro (firma de mail.example.com para un From de example.com)."""
    return bool(domain) and (
        domain == sender_domain
        or sender_domain.endswith("." + domain)
        or domain.endswith("." + sender_domain)
    )


def _sender_authenticated(auth_results: str | None, address: str) -> bool:
    """True si Gmail reporta DMARC aprobado, o DKIM/SPF aprobado con un
    dominio alineado con el del From."""
    if not auth_results or "@" not in address:
        return False
    text = re.sub(r"\s+", " ", auth_results.lower())
    if re.search(r"\bdmarc=pass\b", text):
        return True
    sender_domain = address.rsplit("@", 1)[1]
    for dkim in re.finditer(r"\bdkim=pass\b[^;]*?\bheader\.[di]=@?([^\s;]+)", text):
        if _aligned(dkim.group(1), sender_domain):
            return True
    for spf in re.finditer(r"\bspf=pass\b[^;]*?\bsmtp\.mailfrom=(?:[^\s;@]*@)?([^\s;]+)", text):
        if _aligned(spf.group(1), sender_domain):
            return True
    return False


def parse_message(raw: dict[str, Any]) -> ParsedEmail:
    payload = raw.get("payload") or {}
    raw_headers = payload.get("headers", [])
    headers = {h["name"].lower(): h.get("value", "") for h in raw_headers if "name" in h}
    name, address = parseaddr(headers.get("from", ""))
    address = address.strip().lower()
    if "@" not in address or re.search(r"[\s,;<>]", address):
        address = ""  # sin destinatario valido: el procesador lo descarta

    return ParsedEmail(
        message_id=raw["id"],
        thread_id=raw.get("threadId"),
        from_address=address,
        from_name=_clean_header(name)[:80],
        subject=_clean_header(headers.get("subject")),
        rfc_message_id=_clean_header(headers.get("message-id")) or None,
        references=_clean_header(headers.get("references")) or None,
        body=extract_body(payload),
        is_automated=_is_automated(headers, address) if address else True,
        sender_authenticated=_sender_authenticated(_trusted_auth_results(raw_headers), address),
    )
