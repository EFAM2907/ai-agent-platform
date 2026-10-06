import base64

from app.gmail.parser import parse_message
from app.gmail.tracking import extract_tracking_candidates


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _message(*, headers: dict[str, str], plain: str | None = None, html: str | None = None):
    parts = []
    if plain is not None:
        parts.append({"mimeType": "text/plain", "body": {"data": _b64(plain)}})
    if html is not None:
        parts.append({"mimeType": "text/html", "body": {"data": _b64(html)}})
    return {
        "id": "m1",
        "threadId": "t1",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [{"name": k, "value": v} for k, v in headers.items()],
            "parts": parts,
        },
    }


BASE_HEADERS = {
    "From": "Camila Restrepo <Camila@Example.com>",
    "Subject": "Consulta",
    "Message-ID": "<abc@mail.example.com>",
}


def test_parse_extracts_sender_subject_and_plain_body():
    parsed = parse_message(_message(headers=BASE_HEADERS, plain="Hola, mi guía es 854321"))

    assert parsed.from_address == "camila@example.com"
    assert parsed.from_name == "Camila Restrepo"
    assert parsed.subject == "Consulta"
    assert parsed.rfc_message_id == "<abc@mail.example.com>"
    assert parsed.thread_id == "t1"
    assert "854321" in parsed.body
    assert not parsed.is_automated


def test_parse_falls_back_to_html_and_drops_blockquote():
    html = "<div>Guía 854321</div><blockquote>Guía 999999 anterior</blockquote>"
    parsed = parse_message(_message(headers=BASE_HEADERS, html=html))

    assert "854321" in parsed.body
    assert "999999" not in parsed.body


def test_parse_strips_quoted_reply_so_our_own_answer_does_not_retrigger():
    plain = (
        "Gracias!\n\n"
        "El lun, 21 sept 2026 a las 10:00, Soporte <soporte@example.com> escribió:\n"
        "> Guía: 854321\n> Estado: En tránsito\n"
    )
    parsed = parse_message(_message(headers=BASE_HEADERS, plain=plain))

    assert parsed.body == "Gracias!"


def test_parse_header_values_never_contain_newlines():
    headers = {**BASE_HEADERS, "Subject": "Hola\r\nBcc: victima@example.com"}
    parsed = parse_message(_message(headers=headers, plain="x"))

    assert "\n" not in parsed.subject and "\r" not in parsed.subject


def test_parse_flags_automated_senders():
    for extra in (
        {"Auto-Submitted": "auto-replied"},
        {"Precedence": "bulk"},
        {"List-Unsubscribe": "<mailto:x@example.com>"},
        {"From": "noreply@example.com"},
        {"From": "MAILER-DAEMON@example.com"},
    ):
        parsed = parse_message(_message(headers={**BASE_HEADERS, **extra}, plain="guía 854321"))
        assert parsed.is_automated, extra


def test_parse_invalid_sender_is_marked_automated_with_empty_address():
    parsed = parse_message(_message(headers={**BASE_HEADERS, "From": "sin correo"}, plain="x"))

    assert parsed.from_address == ""
    assert parsed.is_automated


def test_tracking_finds_number_after_keyword_as_explicit():
    candidates = extract_tracking_candidates("Hola, mi guía 854321 no ha llegado")

    assert [(c.number, c.explicit) for c in candidates] == [("854321", True)]


def test_tracking_generic_word_envio_or_other_lines_are_not_explicit():
    # "envio" a secas, o una palabra clave en otra linea, no vuelven
    # "guia" a un telefono cualquiera.
    assert not extract_tracking_candidates("Mi envío\nLlámame al 3001234567")[0].explicit
    assert not extract_tracking_candidates("Tengo una guía.\n3001234567")[0].explicit


def test_tracking_number_de_envio_is_explicit():
    candidates = extract_tracking_candidates("Mi número de envío es 854321")

    assert [(c.number, c.explicit) for c in candidates] == [("854321", True)]


def test_tracking_puts_explicit_candidates_first():
    text = "Mi cédula es 1020304050. Tracking: 854321"

    candidates = extract_tracking_candidates(text)

    assert [c.number for c in candidates] == ["854321", "1020304050"]
    assert candidates[0].explicit and not candidates[1].explicit


def test_tracking_ignores_numbers_inside_emails_urls_and_words():
    text = "escribe a user123456@example.com o ve https://x.com/pedido/7654321/ ref AB1234567"

    assert extract_tracking_candidates(text) == []


def test_tracking_ignores_too_short_and_too_long_numbers():
    assert extract_tracking_candidates("codigo 12345 y 1234567890123456789") == []


def test_tracking_deduplicates_and_caps_results():
    text = "guía 111111 guía 111111 " + " ".join(str(200000 + i) for i in range(10))

    candidates = extract_tracking_candidates(text)

    assert len(candidates) == 5
    assert [c.number for c in candidates].count("111111") == 1


# --- autenticacion del remitente (Authentication-Results de Gmail) -------------


def _auth(value: str | None, *, sender="Camila <camila@example.com>", extra_first=None):
    headers = []
    if value is not None:
        headers.append({"name": "Authentication-Results", "value": value})
    headers += [
        {"name": "From", "value": sender},
        {"name": "Subject", "value": "x"},
    ]
    if extra_first:  # cabecera falsificada por quien envia: va DESPUES de la de Gmail
        headers.append({"name": "Authentication-Results", "value": extra_first})
    raw = {"id": "m1", "payload": {"mimeType": "text/plain", "headers": headers, "body": {}}}
    return parse_message(raw).sender_authenticated


def test_auth_dmarc_pass_is_authenticated():
    assert _auth("mx.google.com; dmarc=pass (p=NONE sp=NONE dis=NONE) header.from=example.com")


def test_auth_aligned_dkim_or_spf_pass_is_authenticated_without_dmarc():
    assert _auth("mx.google.com; dkim=pass header.i=@mail.example.com header.s=k1")
    assert _auth("mx.google.com; spf=pass (google.com: ok) smtp.mailfrom=bounce@example.com")


def test_auth_pass_for_an_unrelated_domain_does_not_count():
    # DKIM/SPF aprobados para el dominio del atacante, no para el del From.
    assert not _auth(
        "mx.google.com; dkim=pass header.i=@attacker.com; spf=pass smtp.mailfrom=x@attacker.com"
    )


def test_auth_failures_missing_or_foreign_authserv_are_not_authenticated():
    assert not _auth("mx.google.com; dmarc=fail (p=NONE) header.from=example.com")
    assert not _auth(None)
    assert not _auth("evil.example.net; dmarc=pass header.from=example.com")


def test_auth_forged_second_header_cannot_override_gmails_verdict():
    assert not _auth(
        "mx.google.com; dmarc=fail header.from=example.com",
        extra_first="mx.google.com; dmarc=pass header.from=example.com",
    )


# --- formatos de guia -----------------------------------------------------------


def test_tracking_accepts_alphanumeric_code_after_keyword_as_explicit():
    for text, expected in (
        ("Mi guía IR-2024-00123 no llega", "IR-2024-00123"),
        ("tracking: IR123456CO", "IR123456CO"),
        ("Guía es ir-2024-00123.", "ir-2024-00123"),
    ):
        [candidate] = extract_tracking_candidates(text)
        assert (candidate.number, candidate.explicit) == (expected, True), text


def test_tracking_alphanumeric_code_without_keyword_is_not_a_candidate_by_default():
    assert extract_tracking_candidates("ref IR-2024-00123 gracias") == []


def test_tracking_bare_pattern_is_configurable(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "gmail_tracking_bare_pattern", r"[A-Z]{2}-\d{4}-\d{5}")

    [candidate] = extract_tracking_candidates("ref IR-2024-00123 gracias")

    assert (candidate.number, candidate.explicit) == ("IR-2024-00123", False)


def test_tracking_keyword_followed_by_plain_words_is_not_a_code():
    assert extract_tracking_candidates("guía es a2 o algo así") == []


def test_tracking_number_inside_explicit_code_is_not_a_second_candidate():
    candidates = extract_tracking_candidates("guía IR-20240001234")

    assert [c.number for c in candidates] == ["IR-20240001234"]


# --- ¿pregunta por el estado de un envio? ---------------------------------------


def test_status_inquiry_detects_questions_about_an_existing_shipment():
    from app.gmail.tracking import looks_like_status_inquiry

    for text in (
        "Hola, ¿cómo va mi envío?",
        "¿Dónde está mi paquete?",
        "Mi pedido no ha llegado",
        "Quiero rastrear mi encomienda",
        "Necesito saber por mi pedido",
        "Tengo un problema con mi guía",
    ):
        assert looks_like_status_inquiry(text), text


def test_status_inquiry_ignores_general_questions_and_unrelated_mail():
    from app.gmail.tracking import looks_like_status_inquiry

    for text in (
        "¿Hacen envíos a Cali?",
        "Quiero hacer un envío a Bogotá, ¿cuánto cuesta?",
        "Hola sin guía",
        "Te paso el informe del trimestre",
        "Almorzamos mañana?",
    ):
        assert not looks_like_status_inquiry(text), text
