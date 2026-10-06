import base64
from datetime import datetime, timedelta, timezone
from email import message_from_bytes, policy

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.crypto import decrypt_secret
from app.core.security import create_access_token
from app.gmail.client import GmailClient
from app.gmail.errors import (
    GmailAccessRevokedError,
    GmailAuthorizationError,
    GmailNotConnectedError,
)
from app.gmail.models import (
    OUTCOME_FAILED,
    OUTCOME_REPLIED,
    OUTCOME_SKIPPED,
    GmailProcessedMessage,
)
from app.gmail.oauth import REQUIRED_SCOPES, GoogleOAuth, OAuthTokens, create_state
from app.gmail.repository import GmailRepository
from app.gmail.service import GmailConnectionService, GmailPollService
from app.users.models import UserRole
from tests.factories import create_organization, create_user


class FakeOAuth:
    def __init__(self, refresh_token="refresh-secret", refresh_error=None):
        self.refresh_token = refresh_token
        self.refresh_error = refresh_error
        self.revoked_tokens: list[str] = []

    def build_authorization_url(self, state):
        return f"https://accounts.example/auth?state={state}"

    async def exchange_code(self, code):
        return OAuthTokens(
            access_token="access", refresh_token=self.refresh_token, scopes=frozenset(REQUIRED_SCOPES)
        )

    async def refresh_access_token(self, refresh_token):
        if self.refresh_error:
            raise self.refresh_error
        return "fresh-access"

    async def revoke(self, token):
        self.revoked_tokens.append(token)


class FakeClient:
    def __init__(self, email="soporte@empresa.com"):
        self.email = email
        self.sent: list[tuple[str, str | None]] = []

    async def get_profile_email(self):
        return self.email

    async def send_message(self, raw_base64url, thread_id):
        self.sent.append((raw_base64url, thread_id))
        return "sent-id"


def _service(db_session, oauth=None, email="soporte@empresa.com", client=None):
    return GmailConnectionService(
        GmailRepository(db_session),
        db_session,
        oauth=oauth or FakeOAuth(),
        client_factory=lambda token: client or FakeClient(email),
    )


async def _connect(db_session, org, user, service):
    state = create_state(user.id, org.id)
    return await service.complete_authorization("code", state)


@pytest.mark.asyncio
async def test_complete_authorization_stores_refresh_token_encrypted(db_session):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)

    connection = await _connect(db_session, org, admin, _service(db_session))

    assert connection.organization_id == org.id
    assert connection.google_email == "soporte@empresa.com"
    assert connection.refresh_token != "refresh-secret"
    assert decrypt_secret(connection.refresh_token) == "refresh-secret"


@pytest.mark.asyncio
async def test_complete_authorization_fails_without_refresh_token(db_session):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    service = _service(db_session, oauth=FakeOAuth(refresh_token=None))

    with pytest.raises(GmailAuthorizationError):
        await _connect(db_session, org, admin, service)

    assert await GmailRepository(db_session).get_connection(org.id) is None


@pytest.mark.asyncio
async def test_complete_authorization_rejects_bad_state(db_session):
    with pytest.raises(GmailAuthorizationError):
        await _service(db_session).complete_authorization("code", "forged")


async def _add_processed(db_session, connection, message_id="m1"):
    db_session.add(
        GmailProcessedMessage(
            connection_id=connection.id,
            organization_id=connection.organization_id,
            gmail_message_id=message_id,
            outcome=OUTCOME_REPLIED,
        )
    )
    await db_session.commit()


async def _processed_count(db_session):
    return len((await db_session.execute(select(GmailProcessedMessage))).scalars().all())


async def _add_row(
    db_session,
    connection,
    *,
    message_id: str,
    outcome: str,
    detail: str | None = None,
    sender: str = "cliente@example.com",
    thread_id: str | None = "thread-1",
    minutes_ago: float = 0,
) -> GmailProcessedMessage:
    row = GmailProcessedMessage(
        connection_id=connection.id,
        organization_id=connection.organization_id,
        gmail_message_id=message_id,
        thread_id=thread_id,
        sender=sender,
        outcome=outcome,
        detail=detail,
        processed_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    )
    db_session.add(row)
    await db_session.commit()
    return row


@pytest.mark.asyncio
async def test_reconnecting_same_mailbox_keeps_history_so_nothing_is_answered_twice(db_session):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    service = _service(db_session)
    connection = await _connect(db_session, org, admin, service)
    await _add_processed(db_session, connection)
    await service.disconnect(org.id)

    reconnected = await _connect(db_session, org, admin, service)

    assert reconnected.id == connection.id
    assert reconnected.revoked_at is None
    assert await _processed_count(db_session) == 1


@pytest.mark.asyncio
async def test_connecting_a_different_mailbox_discards_old_history(db_session):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    connection = await _connect(db_session, org, admin, _service(db_session))
    await _add_processed(db_session, connection)

    await _connect(db_session, org, admin, _service(db_session, email="otra@empresa.com"))

    assert await _processed_count(db_session) == 0


@pytest.mark.asyncio
async def test_disconnect_revokes_at_google_and_deactivates(db_session):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    oauth = FakeOAuth()
    service = _service(db_session, oauth=oauth)
    await _connect(db_session, org, admin, service)

    assert await service.disconnect(org.id) is True

    assert oauth.revoked_tokens == ["refresh-secret"]
    assert await service.get_active_connection(org.id) is None
    assert await service.disconnect(org.id) is False


@pytest.mark.asyncio
async def test_poll_marks_connection_revoked_when_google_rejects_the_token(db_session):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    connection = await _connect(db_session, org, admin, _service(db_session))
    poll = GmailPollService(
        GmailRepository(db_session),
        db_session,
        oauth=FakeOAuth(refresh_error=GmailAccessRevokedError("invalid_grant")),
    )

    with pytest.raises(GmailNotConnectedError):
        await poll.poll(connection, processor=None)  # type: ignore[arg-type]

    assert connection.revoked_at is not None
    with pytest.raises(GmailNotConnectedError):  # y ya no se intenta mas
        await poll.poll(connection, processor=None)  # type: ignore[arg-type]


# -- list_unresolved (cerrar el ciclo de lo que quedo sin responder) ---


@pytest.mark.asyncio
async def test_list_unresolved_returns_skipped_and_failed_but_not_replied(db_session):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    connection = await _connect(db_session, org, admin, _service(db_session))
    await _add_row(db_session, connection, message_id="m1", outcome=OUTCOME_SKIPPED, detail="no_tracking_number", minutes_ago=2)
    await _add_row(db_session, connection, message_id="m2", outcome=OUTCOME_FAILED, detail="RuntimeError", minutes_ago=1)
    await _add_row(db_session, connection, message_id="m3", outcome=OUTCOME_REPLIED, detail="status", minutes_ago=0)

    rows, total = await GmailRepository(db_session).list_unresolved(connection.id)

    assert total == 2
    assert [r.gmail_message_id for r in rows] == ["m2", "m1"]  # mas reciente primero


@pytest.mark.asyncio
async def test_list_unresolved_is_scoped_to_the_connection(db_session):
    org_a = await create_organization(db_session, name="A")
    org_b = await create_organization(db_session, name="B")
    admin_a = await create_user(db_session, org_a.id, role=UserRole.ADMIN)
    admin_b = await create_user(db_session, org_b.id, role=UserRole.ADMIN)
    conn_a = await _connect(db_session, org_a, admin_a, _service(db_session))
    conn_b = await _connect(db_session, org_b, admin_b, _service(db_session))
    await _add_row(db_session, conn_a, message_id="a1", outcome=OUTCOME_SKIPPED)
    await _add_row(db_session, conn_b, message_id="b1", outcome=OUTCOME_SKIPPED)

    rows, total = await GmailRepository(db_session).list_unresolved(conn_a.id)

    assert total == 1
    assert rows[0].gmail_message_id == "a1"


@pytest.mark.asyncio
async def test_gmail_unresolved_endpoint_returns_a_gmail_link_and_no_body(
    db_session, client, google_configured
):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    connection = await _connect(db_session, org, admin, _service(db_session))
    await _add_row(
        db_session,
        connection,
        message_id="m1",
        outcome=OUTCOME_SKIPPED,
        detail="no_tracking_number",
        sender="curioso@example.com",
        thread_id="thread-xyz",
    )
    await db_session.commit()

    response = await client.get("/gmail/unresolved", headers=_auth(admin, org))

    assert response.status_code == 200
    body = response.json()
    assert (body["total"], body["returned"]) == (1, 1)
    [msg] = body["messages"]
    assert msg["sender"] == "curioso@example.com"
    assert msg["detail"] == "no_tracking_number"
    assert msg["gmail_link"] == "https://mail.google.com/mail/?authuser=soporte@empresa.com#all/thread-xyz"
    assert "body" not in msg and "subject" not in msg


@pytest.mark.asyncio
async def test_gmail_unresolved_requires_admin(db_session, client, google_configured):
    org = await create_organization(db_session)
    member = await create_user(db_session, org.id, role=UserRole.MEMBER)
    await db_session.commit()

    response = await client.get("/gmail/unresolved", headers=_auth(member, org))

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_gmail_unresolved_is_empty_without_a_connection(db_session, client):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    await db_session.commit()

    response = await client.get("/gmail/unresolved", headers=_auth(admin, org))

    assert response.status_code == 200
    assert response.json() == {"total": 0, "returned": 0, "messages": []}


# -- send_email (invitacion de create_user) ----------------------------


@pytest.mark.asyncio
async def test_send_email_uses_the_connected_mailbox_as_sender(db_session):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    client = FakeClient()
    service = _service(db_session, client=client)
    await _connect(db_session, org, admin, service)

    await service.send_email(
        org.id, to_email="nuevo@example.com", subject="Asunto", body="Cuerpo del correo"
    )

    [(raw, thread_id)] = client.sent
    assert thread_id is None
    sent = message_from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)), policy=policy.default)
    assert sent["From"] == "soporte@empresa.com"
    assert sent["To"] == "nuevo@example.com"
    assert sent["Subject"] == "Asunto"
    assert sent.get_body(("plain",)).get_content().strip() == "Cuerpo del correo"


@pytest.mark.asyncio
async def test_send_email_without_a_connection_raises_not_connected(db_session):
    org = await create_organization(db_session)
    service = _service(db_session)

    with pytest.raises(GmailNotConnectedError):
        await service.send_email(org.id, to_email="x@example.com", subject="s", body="b")


@pytest.mark.asyncio
async def test_send_email_marks_connection_revoked_when_google_rejects_the_token(db_session):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    connection = await _connect(db_session, org, admin, _service(db_session))
    service = _service(
        db_session, oauth=FakeOAuth(refresh_error=GmailAccessRevokedError("invalid_grant"))
    )

    with pytest.raises(GmailNotConnectedError):
        await service.send_email(org.id, to_email="x@example.com", subject="s", body="b")

    assert connection.revoked_at is not None


# -- endpoints ---------------------------------------------------------


def _auth(user, org):
    token = create_access_token({"sub": str(user.id), "organization_id": str(org.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def google_configured(monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "client-id")
    monkeypatch.setattr(settings, "google_client_secret", "client-secret")


@pytest.mark.asyncio
async def test_connect_returns_google_consent_url_for_admins(db_session, client, google_configured):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    await db_session.commit()

    response = await client.get("/gmail/connect", headers=_auth(admin, org))

    assert response.status_code == 200
    assert response.json()["authorization_url"].startswith("https://accounts.google.com/")


@pytest.mark.asyncio
async def test_connect_is_503_when_google_credentials_are_missing(db_session, client, monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", None)
    monkeypatch.setattr(settings, "google_client_secret", None)
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    await db_session.commit()

    response = await client.get("/gmail/connect", headers=_auth(admin, org))

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_gmail_endpoints_require_admin(db_session, client, google_configured):
    org = await create_organization(db_session)
    member = await create_user(db_session, org.id, role=UserRole.MEMBER)
    await db_session.commit()
    headers = _auth(member, org)

    assert (await client.get("/gmail/connect", headers=headers)).status_code == 403
    assert (await client.get("/gmail/status", headers=headers)).status_code == 403
    assert (await client.post("/gmail/poll", headers=headers)).status_code == 403
    assert (await client.delete("/gmail/connection", headers=headers)).status_code == 403


@pytest.mark.asyncio
async def test_gmail_endpoints_require_authentication(client):
    assert (await client.get("/gmail/connect")).status_code in (401, 403)
    assert (await client.get("/gmail/status")).status_code in (401, 403)


@pytest.mark.asyncio
async def test_callback_rejects_missing_or_forged_state(client):
    assert (await client.get("/gmail/callback", params={"error": "access_denied"})).status_code == 400
    assert (await client.get("/gmail/callback")).status_code == 400
    forged = await client.get("/gmail/callback", params={"code": "c", "state": "forged"})
    assert forged.status_code == 400


@pytest.mark.asyncio
async def test_full_oauth_flow_then_status_then_disconnect(
    db_session, client, google_configured, monkeypatch
):
    async def fake_exchange(self, code):
        return OAuthTokens("access", "refresh-secret", frozenset(REQUIRED_SCOPES))

    async def fake_profile(self):
        return "soporte@empresa.com"

    revoked: list[str] = []

    async def fake_revoke(self, token):
        revoked.append(token)

    monkeypatch.setattr(GoogleOAuth, "exchange_code", fake_exchange)
    monkeypatch.setattr(GoogleOAuth, "revoke", fake_revoke)
    monkeypatch.setattr(GmailClient, "get_profile_email", fake_profile)

    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.OWNER)
    await db_session.commit()
    headers = _auth(admin, org)

    assert (await client.get("/gmail/status", headers=headers)).json() == {
        "connected": False,
        "google_email": None,
        "connected_at": None,
        "last_polled_at": None,
    }

    callback = await client.get(
        "/gmail/callback", params={"code": "c", "state": create_state(admin.id, org.id)}
    )
    assert callback.status_code == 200
    assert "soporte@empresa.com" in callback.text

    status = (await client.get("/gmail/status", headers=headers)).json()
    assert status["connected"] is True
    assert status["google_email"] == "soporte@empresa.com"
    assert "refresh" not in str(status)

    assert (await client.delete("/gmail/connection", headers=headers)).status_code == 204
    assert revoked == ["refresh-secret"]
    assert (await client.get("/gmail/status", headers=headers)).json()["connected"] is False
    assert (await client.delete("/gmail/connection", headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_status_only_shows_own_organization_connection(
    db_session, client, google_configured
):
    org_a = await create_organization(db_session, name="A")
    org_b = await create_organization(db_session, name="B")
    admin_a = await create_user(db_session, org_a.id, role=UserRole.ADMIN)
    admin_b = await create_user(db_session, org_b.id, role=UserRole.ADMIN)
    await db_session.commit()
    await _connect(db_session, org_a, admin_a, _service(db_session))

    assert (await client.get("/gmail/status", headers=_auth(admin_a, org_a))).json()["connected"]
    assert not (await client.get("/gmail/status", headers=_auth(admin_b, org_b))).json()["connected"]


@pytest.mark.asyncio
async def test_poll_without_connection_is_409(db_session, client, google_configured):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    await db_session.commit()

    response = await client.post("/gmail/poll", headers=_auth(admin, org))

    assert response.status_code == 409
