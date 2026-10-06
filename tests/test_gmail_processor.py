"""Pipeline completo de app.gmail.processor contra la BD de tests, con
un Gmail y un LLM falsos: leer -> extraer guia -> consultar envio ->
redactar -> responder al mismo remitente."""

import base64
from email import message_from_bytes, policy
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.gmail.models import (
    MAX_ATTEMPTS,
    OUTCOME_FAILED,
    OUTCOME_REPLIED,
    OUTCOME_SKIPPED,
    GmailConnection,
    GmailProcessedMessage,
)
from app.gmail.processor import InboxProcessor
from app.gmail.reply import ReplyComposer
from app.gmail.repository import GmailRepository
from app.llm.errors import LLMError
from app.shipments.models import ShipmentStatus
from app.shipments.repository import ShipmentRepository
from app.shipments.service import ShipmentService
from tests.factories import create_customer, create_organization, create_shipment, create_user

OWN_EMAIL = "soporte@empresa.com"
CUSTOMER_EMAIL = "camila@example.com"
# Cabecera que Gmail antepone a todo correo entrante.
GMAIL_AUTH_PASS = (
    "mx.google.com; dkim=pass header.i=@example.com; "
    "spf=pass smtp.mailfrom=camila@example.com; dmarc=pass (p=NONE) header.from=example.com"
)


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def make_message(
    message_id: str,
    *,
    body: str,
    sender: str = "Camila Restrepo <camila@example.com>",
    subject: str = "Mi envío",
    extra_headers: dict[str, str] | None = None,
):
    headers = {
        "Authentication-Results": GMAIL_AUTH_PASS,
        "From": sender,
        "Subject": subject,
        "Message-ID": f"<{message_id}@mail.example.com>",
        **(extra_headers or {}),
    }
    return {
        "id": message_id,
        "threadId": f"thread-{message_id}",
        "payload": {
            "mimeType": "text/plain",
            "headers": [{"name": k, "value": v} for k, v in headers.items()],
            "body": {"data": _b64(body)},
        },
    }


class FakeGmail:
    def __init__(self, messages: list[dict]):
        self.messages = {m["id"]: m for m in messages}
        self.sent: list[tuple[str, str | None]] = []
        self.fail_send = False

    async def list_message_ids(self, query, max_results):
        return list(self.messages)[:max_results]

    async def get_message(self, message_id):
        return self.messages[message_id]

    async def send_message(self, raw_base64url, thread_id):
        if self.fail_send:
            raise RuntimeError("gmail down")
        self.sent.append((raw_base64url, thread_id))
        return "sent-id"

    def sent_email(self, index=-1):
        raw = self.sent[index][0]
        return message_from_bytes(
            base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)), policy=policy.default
        )


class FakeLLM:
    """Devuelve `content` tal cual, o levanta `error`. Guarda el ultimo
    request para inspeccionar que le llego al modelo."""

    def __init__(self, content: str | None = None, error: Exception | None = None):
        self.content = content
        self.error = error
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return SimpleNamespace(content=self.content)


def _body_text(email_message) -> str:
    return email_message.get_body(("plain",)).get_content()


async def _setup(db_session, *, llm=None, gmail_messages=(), **shipment_kwargs):
    org = await create_organization(db_session)
    user = await create_user(db_session, org.id)
    customer = await create_customer(
        db_session, org.id, full_name="Camila Restrepo", email=CUSTOMER_EMAIL
    )
    await create_shipment(
        db_session,
        org.id,
        customer.id,
        tracking_number="854321",
        status=shipment_kwargs.get("status", ShipmentStatus.EN_TRANSITO),
        origin_city="Medellín",
        destination_city="Bogotá",
    )
    connection = GmailConnection(
        organization_id=org.id,
        connected_by_user_id=user.id,
        google_email=OWN_EMAIL,
        refresh_token="encrypted",
    )
    db_session.add(connection)
    await db_session.commit()

    llm = llm or FakeLLM(
        "Hola Camila,\n\nTu guía 854321 va así:\nEstado: En tránsito - Medellín → Bogotá\n\nSaludos."
    )
    processor = InboxProcessor(
        repository=GmailRepository(db_session),
        shipment_service=ShipmentService(ShipmentRepository(db_session), db_session),
        composer=ReplyComposer(llm, "cheap-model", tenant_id=str(org.id)),
    )
    return org, connection, processor, FakeGmail(list(gmail_messages)), llm


async def _rows(db_session):
    return list((await db_session.execute(select(GmailProcessedMessage))).scalars().all())


@pytest.mark.asyncio
async def test_replies_to_same_sender_in_same_thread_with_status_and_route(db_session):
    msg = make_message(
        "m1",
        body="Hola, quiero saber por mi guía 854321 por favor",
        extra_headers={"Reply-To": "otro@attacker.com", "Cc": "copia@example.com"},
    )
    _, connection, processor, gmail, llm = await _setup(db_session, gmail_messages=[msg])

    summary = await processor.process(connection, gmail)

    assert (summary.examined, summary.replied, summary.failed) == (1, 1, 0)
    assert len(gmail.sent) == 1
    raw, thread_id = gmail.sent[0]
    assert thread_id == "thread-m1"

    reply = gmail.sent_email()
    # AL MISMO REMITENTE: el From del correo original, no Reply-To ni Cc.
    assert reply["To"] == "camila@example.com"
    assert reply["Cc"] is None
    assert reply["From"] == OWN_EMAIL
    assert reply["Subject"] == "Re: Mi envío"
    assert reply["In-Reply-To"] == "<m1@mail.example.com>"
    assert reply["References"] == "<m1@mail.example.com>"
    assert reply["Auto-Submitted"] == "auto-replied"
    assert "Estado: En tránsito - Medellín → Bogotá" in _body_text(reply)

    [row] = await _rows(db_session)
    assert (row.outcome, row.tracking_number, row.sender) == (
        OUTCOME_REPLIED,
        "854321",
        "camila@example.com",
    )


@pytest.mark.asyncio
async def test_llm_prompt_gets_verified_facts_but_no_customer_contact_data(db_session):
    msg = make_message("m1", body="guía 854321")
    _, connection, processor, gmail, llm = await _setup(db_session, gmail_messages=[msg])

    await processor.process(connection, gmail)

    system_prompt = llm.requests[0].messages[0].content
    assert "Estado: En tránsito - Medellín → Bogotá" in system_prompt
    assert "854321" in system_prompt
    assert llm.requests[0].model == "cheap-model"
    # El cuerpo del correo (entrada no confiable) nunca llega al modelo.
    assert "guía 854321" not in system_prompt
    assert "camila@example.com" not in system_prompt


@pytest.mark.asyncio
async def test_same_message_is_never_answered_twice(db_session):
    msg = make_message("m1", body="guía 854321")
    _, connection, processor, gmail, _ = await _setup(db_session, gmail_messages=[msg])

    first = await processor.process(connection, gmail)
    second = await processor.process(connection, gmail)

    assert first.replied == 1
    assert (second.examined, second.replied) == (0, 0)
    assert len(gmail.sent) == 1


@pytest.mark.asyncio
async def test_unknown_explicit_tracking_number_gets_not_found_reply(db_session):
    msg = make_message("m1", body="Mi guía 999999 no aparece")
    _, connection, processor, gmail, llm = await _setup(db_session, gmail_messages=[msg])

    summary = await processor.process(connection, gmail)

    assert summary.replied == 1
    assert "999999" in _body_text(gmail.sent_email())
    assert "No encontramos" in _body_text(gmail.sent_email())
    assert llm.requests == []  # no hay hechos que redactar


@pytest.mark.asyncio
async def test_email_without_tracking_number_is_not_answered(db_session):
    msg = make_message("m1", body="Hola, ¿tienen servicio a Cali?")
    _, connection, processor, gmail, _ = await _setup(db_session, gmail_messages=[msg])

    summary = await processor.process(connection, gmail)

    assert gmail.sent == []
    assert summary.skip_reasons == {"no_tracking_number": 1}


@pytest.mark.asyncio
async def test_bare_number_that_is_not_a_shipment_is_not_answered(db_session):
    # Un telefono suelto (sin palabra clave de guia) no justifica responder.
    msg = make_message("m1", body="Llámame al 3001234567")
    _, connection, processor, gmail, _ = await _setup(db_session, gmail_messages=[msg])

    await processor.process(connection, gmail)

    assert gmail.sent == []


@pytest.mark.asyncio
async def test_bare_number_matching_a_shipment_is_answered(db_session):
    msg = make_message("m1", body="Buenas, 854321 ¿dónde va?")
    _, connection, processor, gmail, _ = await _setup(db_session, gmail_messages=[msg])

    summary = await processor.process(connection, gmail)

    assert summary.replied == 1


@pytest.mark.asyncio
async def test_never_answers_automated_senders_or_own_messages(db_session):
    messages = [
        make_message("auto", body="guía 854321", sender="noreply@example.com"),
        make_message("bulk", body="guía 854321", extra_headers={"Precedence": "bulk"}),
        make_message("own", body="guía 854321", sender=f"Yo <{OWN_EMAIL}>"),
    ]
    _, connection, processor, gmail, _ = await _setup(db_session, gmail_messages=messages)

    summary = await processor.process(connection, gmail)

    assert gmail.sent == []
    assert summary.skip_reasons == {"automated_sender": 2, "own_message": 1}


@pytest.mark.asyncio
async def test_sender_is_rate_limited_per_day(db_session, monkeypatch):
    monkeypatch.setattr(settings, "gmail_max_replies_per_sender_per_day", 2)
    messages = [make_message(f"m{i}", body="guía 854321") for i in range(4)]
    _, connection, processor, gmail, _ = await _setup(db_session, gmail_messages=messages)

    summary = await processor.process(connection, gmail)

    assert summary.replied == 2
    assert summary.skip_reasons == {"sender_rate_limited": 2}


@pytest.mark.asyncio
async def test_only_finds_shipments_of_the_connected_organization(db_session):
    msg = make_message("m1", body="guía 777777")
    org, connection, processor, gmail, _ = await _setup(db_session, gmail_messages=[msg])
    other_org = await create_organization(db_session, name="Otra")
    other_customer = await create_customer(db_session, other_org.id)
    await create_shipment(db_session, other_org.id, other_customer.id, tracking_number="777777")
    await db_session.commit()

    await processor.process(connection, gmail)

    # La guia existe, pero en otro tenant: se responde "no encontrada".
    assert "No encontramos" in _body_text(gmail.sent_email())
    assert "Estado:" not in _body_text(gmail.sent_email())


@pytest.mark.asyncio
async def test_llm_failure_falls_back_to_fixed_template_with_correct_facts(db_session):
    msg = make_message("m1", body="guía 854321")
    llm = FakeLLM(error=LLMError("gateway down"))
    _, connection, processor, gmail, _ = await _setup(db_session, llm=llm, gmail_messages=[msg])

    summary = await processor.process(connection, gmail)

    assert summary.replied == 1
    body = _body_text(gmail.sent_email())
    assert "Estado: En tránsito - Medellín → Bogotá" in body
    assert "854321" in body


@pytest.mark.asyncio
async def test_llm_output_that_drops_the_facts_is_replaced_by_the_template(db_session):
    msg = make_message("m1", body="guía 854321")
    llm = FakeLLM("¡Hola! Tu paquete ya casi llega, llegará mañana sin falta.")
    _, connection, processor, gmail, _ = await _setup(db_session, llm=llm, gmail_messages=[msg])

    await processor.process(connection, gmail)

    body = _body_text(gmail.sent_email())
    assert "llegará mañana" not in body
    assert "Estado: En tránsito - Medellín → Bogotá" in body


@pytest.mark.asyncio
async def test_failed_send_is_retried_on_next_poll_up_to_the_attempt_cap(db_session):
    msg = make_message("m1", body="guía 854321")
    _, connection, processor, gmail, _ = await _setup(db_session, gmail_messages=[msg])
    gmail.fail_send = True

    for _attempt in range(MAX_ATTEMPTS + 2):
        await processor.process(connection, gmail)

    [row] = await _rows(db_session)
    assert row.outcome == OUTCOME_FAILED
    assert row.attempts == MAX_ATTEMPTS  # se rinde: no reintenta para siempre

    gmail.fail_send = False
    await processor.process(connection, gmail)
    assert gmail.sent == []  # ya agotado, ni siquiera lo vuelve a intentar


@pytest.mark.asyncio
async def test_failed_send_recovers_when_gmail_comes_back(db_session):
    msg = make_message("m1", body="guía 854321")
    _, connection, processor, gmail, _ = await _setup(db_session, gmail_messages=[msg])
    gmail.fail_send = True
    await processor.process(connection, gmail)
    gmail.fail_send = False

    summary = await processor.process(connection, gmail)

    assert summary.replied == 1
    [row] = await _rows(db_session)
    assert (row.outcome, row.attempts) == (OUTCOME_REPLIED, 2)


@pytest.mark.asyncio
async def test_one_broken_message_does_not_stop_the_others(db_session):
    good = make_message("good", body="guía 854321", sender="ana@example.com")
    _, connection, processor, gmail, _ = await _setup(db_session, gmail_messages=[good])
    gmail.messages = {"broken": {"id": "broken", "payload": None}, **gmail.messages}

    async def get_message(message_id):
        if message_id == "broken":
            raise ValueError("malformed")
        return gmail.messages[message_id]

    gmail.get_message = get_message

    summary = await processor.process(connection, gmail)

    assert (summary.replied, summary.failed) == (1, 1)


@pytest.mark.asyncio
async def test_skipped_messages_are_recorded_and_not_reexamined(db_session):
    msg = make_message("m1", body="hola sin guía")
    _, connection, processor, gmail, _ = await _setup(db_session, gmail_messages=[msg])

    await processor.process(connection, gmail)
    second = await processor.process(connection, gmail)

    [row] = await _rows(db_session)
    assert (row.outcome, row.detail) == (OUTCOME_SKIPPED, "no_tracking_number")
    assert second.examined == 0


# --- el remitente debe ser el cliente del envio ------------------------------------


@pytest.mark.asyncio
async def test_status_is_only_shared_with_the_customer_of_that_shipment(db_session):
    msg = make_message(
        "m1",
        body="guía 854321",
        sender="Curioso <otro@example.com>",
        extra_headers={"Authentication-Results": GMAIL_AUTH_PASS.replace("camila", "otro")},
    )
    _, connection, processor, gmail, llm = await _setup(db_session, gmail_messages=[msg])

    summary = await processor.process(connection, gmail)

    assert summary.replied == 1
    body = _body_text(gmail.sent_email())
    assert "No encontramos" in body and "Estado:" not in body
    assert llm.requests == []


@pytest.mark.asyncio
async def test_unknown_and_foreign_guides_get_the_exact_same_reply(db_session):
    # Que la respuesta cambie entre "no existe" y "no es tuya" confirmaria
    # a un tercero que la guia es real.
    foreign = make_message("m1", body="guía 854321", sender="otro@example.com")
    unknown = make_message("m2", body="guía 999999", sender="otro2@example.com")
    _, connection, processor, gmail, _ = await _setup(db_session, gmail_messages=[foreign, unknown])

    await processor.process(connection, gmail)

    a, b = _body_text(gmail.sent_email(0)), _body_text(gmail.sent_email(1))
    assert a.replace("854321", "X") == b.replace("999999", "X")


@pytest.mark.asyncio
async def test_forged_from_without_gmail_authentication_gets_no_status(db_session):
    # El From coincide con el cliente, pero Gmail no aprobo SPF/DKIM/DMARC.
    msg = make_message(
        "m1",
        body="guía 854321",
        extra_headers={
            "Authentication-Results": "mx.google.com; dmarc=fail header.from=example.com"
        },
    )
    _, connection, processor, gmail, _ = await _setup(db_session, gmail_messages=[msg])

    await processor.process(connection, gmail)

    assert "Estado:" not in _body_text(gmail.sent_email())


@pytest.mark.asyncio
async def test_customer_email_match_is_case_insensitive(db_session):
    msg = make_message("m1", body="guía 854321", sender="Camila <CAMILA@Example.com>")
    _, connection, processor, gmail, _ = await _setup(db_session, gmail_messages=[msg])

    await processor.process(connection, gmail)

    assert "Estado: En tránsito - Medellín → Bogotá" in _body_text(gmail.sent_email())


@pytest.mark.asyncio
async def test_shipment_of_a_customer_without_email_is_never_shared(db_session):
    msg = make_message("m1", body="guía 555555")
    org, connection, processor, gmail, _ = await _setup(db_session, gmail_messages=[msg])
    nameless = await create_customer(db_session, org.id, full_name="Sin correo")
    await create_shipment(db_session, org.id, nameless.id, tracking_number="555555")
    await db_session.commit()

    await processor.process(connection, gmail)

    assert "Estado:" not in _body_text(gmail.sent_email())


@pytest.mark.asyncio
async def test_sender_match_can_be_turned_off(db_session, monkeypatch):
    monkeypatch.setattr(settings, "gmail_require_sender_match", False)
    msg = make_message("m1", body="guía 854321", sender="otro@example.com")
    _, connection, processor, gmail, _ = await _setup(db_session, gmail_messages=[msg])

    await processor.process(connection, gmail)

    assert "Estado: En tránsito - Medellín → Bogotá" in _body_text(gmail.sent_email())


# --- correo sin guia ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_inquiry_without_a_guide_gets_a_request_for_the_number(db_session):
    msg = make_message("m1", body="Hola, ¿cómo va mi envío?")
    _, connection, processor, gmail, llm = await _setup(db_session, gmail_messages=[msg])

    summary = await processor.process(connection, gmail)

    assert summary.replied == 1
    body = _body_text(gmail.sent_email())
    assert "número de guía" in body
    assert "correo electrónico registrado" in body
    assert llm.requests == []  # plantilla fija: el correo del tercero no llega a ningun modelo
    [row] = await _rows(db_session)
    assert (row.outcome, row.detail, row.tracking_number) == (
        OUTCOME_REPLIED,
        "tracking_request",
        None,
    )


@pytest.mark.asyncio
async def test_guide_request_is_limited_to_one_per_sender_per_day(db_session):
    messages = [make_message(f"m{i}", body="¿Dónde está mi paquete?") for i in range(3)]
    _, connection, processor, gmail, _ = await _setup(db_session, gmail_messages=messages)

    summary = await processor.process(connection, gmail)

    assert summary.replied == 1
    assert summary.skip_reasons == {"tracking_request_rate_limited": 2}


@pytest.mark.asyncio
async def test_guide_request_does_not_block_the_status_reply_that_follows(db_session):
    first = make_message("m1", body="¿Dónde está mi paquete?")
    second = make_message("m2", body="Es la 854321")
    _, connection, processor, gmail, _ = await _setup(db_session, gmail_messages=[first, second])

    summary = await processor.process(connection, gmail)

    assert summary.replied == 2
    assert "Estado: En tránsito - Medellín → Bogotá" in _body_text(gmail.sent_email(1))


@pytest.mark.asyncio
async def test_general_questions_and_unrelated_mail_still_get_no_reply(db_session):
    messages = [
        make_message("m1", body="¿Hacen envíos a Cali?"),
        make_message("m2", body="Almorzamos mañana?", sender="amigo@example.com"),
    ]
    _, connection, processor, gmail, _ = await _setup(db_session, gmail_messages=messages)

    summary = await processor.process(connection, gmail)

    assert gmail.sent == []
    assert summary.skip_reasons == {"no_tracking_number": 2}


# --- guias alfanumericas ---------------------------------------------------------


@pytest.mark.asyncio
async def test_alphanumeric_guide_is_found_whatever_the_case_the_customer_typed(db_session):
    msg = make_message("m1", body="Mi guía ir-2024-00123 ¿dónde va?")
    org, connection, processor, gmail, _ = await _setup(db_session, gmail_messages=[msg])
    customer = await create_customer(db_session, org.id, email=CUSTOMER_EMAIL)
    await create_shipment(
        db_session,
        org.id,
        customer.id,
        tracking_number="IR-2024-00123",
        origin_city="Cali",
        destination_city="Pasto",
    )
    await db_session.commit()

    await processor.process(connection, gmail)

    assert "Estado: En tránsito - Cali → Pasto" in _body_text(gmail.sent_email())
