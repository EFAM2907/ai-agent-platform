"""POST /users/ (REST): mismo comportamiento de envio de la contraseña
temporal por Gmail que ya tenia la tool de chat create_user (ver
FakeGmailService y los tests de mas abajo en
tests/test_user_management_tools.py) -- ahora tambien la API REST usa
app.gmail.outbound.send_user_invite_email, compartido entre ambos
caminos de creacion de usuario en vez de que solo el chat mandara el
correo.
"""

import pytest

from app.core.security import create_access_token
from app.gmail.errors import GmailNotConnectedError
from app.main import app
from app.users.api import get_gmail_connection_service
from app.users.models import UserRole
from tests.factories import create_organization, create_user


class FakeGmailService:
    """Mismo doble que tests/test_user_management_tools.py: solo lo
    que send_user_invite_email llama."""

    def __init__(self, *, error: Exception | None = None):
        self.error = error
        self.calls: list[dict] = []

    async def send_email(self, organization_id, *, to_email, subject, body):
        self.calls.append(
            {
                "organization_id": organization_id,
                "to_email": to_email,
                "subject": subject,
                "body": body,
            }
        )
        if self.error:
            raise self.error


@pytest.mark.asyncio
async def test_create_user_rest_emails_password_when_gmail_connected(db_session, client):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    await db_session.commit()
    token = create_access_token({"sub": str(admin.id), "organization_id": str(org.id)})

    gmail = FakeGmailService()
    app.dependency_overrides[get_gmail_connection_service] = lambda: gmail
    try:
        response = await client.post(
            "/users/",
            json={"email": "nuevo@test.com", "full_name": "Nuevo Usuario", "role": "member"},
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        del app.dependency_overrides[get_gmail_connection_service]

    assert response.status_code == 201
    body = response.json()
    assert body["password_sent_by_email"] is True
    assert body["temporary_password"] is None
    [call] = gmail.calls
    assert call["organization_id"] == org.id
    assert call["to_email"] == "nuevo@test.com"
    assert "Nuevo Usuario" in call["body"]


@pytest.mark.asyncio
async def test_create_user_rest_falls_back_to_password_when_email_fails(db_session, client):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    await db_session.commit()
    token = create_access_token({"sub": str(admin.id), "organization_id": str(org.id)})

    gmail = FakeGmailService(error=GmailNotConnectedError("no gmail"))
    app.dependency_overrides[get_gmail_connection_service] = lambda: gmail
    try:
        response = await client.post(
            "/users/",
            json={"email": "nuevo2@test.com", "full_name": "Nuevo Dos", "role": "member"},
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        del app.dependency_overrides[get_gmail_connection_service]

    assert response.status_code == 201
    body = response.json()
    assert body["password_sent_by_email"] is False
    assert isinstance(body["temporary_password"], str) and body["temporary_password"]
    assert gmail.calls  # se intento, pero fallo -- no bloquea la creacion


@pytest.mark.asyncio
async def test_create_user_rest_shows_password_without_gmail_connected(db_session, client):
    # Sin override de get_gmail_connection_service: usa el real, que
    # llama a GmailConnectionService.send_email de verdad -- sin
    # conexion activa para esta organizacion, esa llamada levanta
    # GmailNotConnectedError, y send_user_invite_email lo atrapa igual
    # que arriba. Cubre el caso mas comun en un tenant nuevo: nadie ha
    # conectado Gmail todavia.
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    await db_session.commit()
    token = create_access_token({"sub": str(admin.id), "organization_id": str(org.id)})

    response = await client.post(
        "/users/",
        json={"email": "nuevo3@test.com", "full_name": "Nuevo Tres", "role": "member"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["password_sent_by_email"] is False
    assert isinstance(body["temporary_password"], str) and body["temporary_password"]
