"""GET /chat/sessions/{id}: cada quien lee su propio historial, nunca
el de otro miembro de la organizacion -- antes de este fix la ruta solo
filtraba por organization_id, asi que un session_id de otro usuario del
mismo tenant (ej. uno que quedo guardado en el localStorage de un
navegador compartido) devolvia esa conversacion completa."""

import uuid

import pytest

from app.chat.models import ChatSession, MessageRole
from app.chat.repository import ChatRepository
from app.core.security import create_access_token
from app.users.models import UserRole
from tests.factories import create_organization, create_user


def _auth(user):
    token = create_access_token({"sub": str(user.id), "organization_id": str(user.organization_id)})
    return {"Authorization": f"Bearer {token}"}


async def _create_session_with_message(db_session, org_id, user_id, content="hola"):
    repo = ChatRepository(db_session)
    session = await repo.create_session(org_id, user_id)
    await repo.add_message(session.id, org_id, MessageRole.USER, content)
    await db_session.commit()
    return session


@pytest.mark.asyncio
async def test_get_session_history_returns_own_session(db_session, client):
    org = await create_organization(db_session)
    user = await create_user(db_session, org.id)
    await db_session.commit()
    session = await _create_session_with_message(db_session, org.id, user.id, "mi mensaje")

    response = await client.get(f"/chat/sessions/{session.id}", headers=_auth(user))

    assert response.status_code == 200
    assert response.json()["messages"][0]["content"] == "mi mensaje"


@pytest.mark.asyncio
async def test_get_session_history_is_404_for_another_users_session_in_the_same_org(
    db_session, client
):
    org = await create_organization(db_session)
    owner = await create_user(db_session, org.id, role=UserRole.OWNER)
    other = await create_user(db_session, org.id, role=UserRole.MEMBER)
    await db_session.commit()
    owners_session = await _create_session_with_message(db_session, org.id, owner.id)

    response = await client.get(f"/chat/sessions/{owners_session.id}", headers=_auth(other))

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_session_history_is_404_for_a_session_from_another_organization(
    db_session, client
):
    org_a = await create_organization(db_session, name="A")
    org_b = await create_organization(db_session, name="B")
    user_a = await create_user(db_session, org_a.id)
    user_b = await create_user(db_session, org_b.id)
    await db_session.commit()
    session_a = await _create_session_with_message(db_session, org_a.id, user_a.id)

    response = await client.get(f"/chat/sessions/{session_a.id}", headers=_auth(user_b))

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_session_history_is_404_for_an_unknown_id(db_session, client):
    org = await create_organization(db_session)
    user = await create_user(db_session, org.id)
    await db_session.commit()

    response = await client.get(f"/chat/sessions/{uuid.uuid4()}", headers=_auth(user))

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_my_profile_reports_must_change_password(db_session, client):
    org = await create_organization(db_session)
    user = await create_user(db_session, org.id, must_change_password=True)
    await db_session.commit()

    response = await client.get("/users/me", headers=_auth(user))

    assert response.status_code == 200
    assert response.json()["must_change_password"] is True
