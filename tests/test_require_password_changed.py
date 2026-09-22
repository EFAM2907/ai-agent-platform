"""require_password_changed bloquea las rutas de negocio (chat) para
una cuenta con must_change_password=True -- sin esto la bandera seria
solo cosmetica: nada impediria llamar la API directo saltandose la
pantalla de cambio de contraseña que el frontend mostraria. Sin
cobertura previa a este cambio.
"""

import pytest
from fastapi import HTTPException

from app.core.dependencies import require_password_changed
from app.core.security import create_access_token
from app.users.models import UserRole
from tests.factories import create_organization, create_user


@pytest.mark.asyncio
async def test_require_password_changed_blocks_when_flag_is_set(db_session):
    org = await create_organization(db_session)
    user = await create_user(db_session, org.id, must_change_password=True)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await require_password_changed(current_user=user)

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_require_password_changed_passes_through_when_flag_is_clear(db_session):
    org = await create_organization(db_session)
    user = await create_user(db_session, org.id, must_change_password=False)
    await db_session.commit()

    result = await require_password_changed(current_user=user)

    assert result is user


@pytest.mark.asyncio
async def test_chat_messages_blocked_for_user_with_pending_password_change(db_session, client):
    org = await create_organization(db_session)
    user = await create_user(db_session, org.id, must_change_password=True)
    await db_session.commit()

    token = create_access_token({"sub": str(user.id), "organization_id": str(org.id)})

    response = await client.post(
        "/chat/messages",
        json={"message": "hola"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert "Password change required" in response.json()["detail"]


@pytest.mark.asyncio
async def test_chat_messages_stream_blocked_for_user_with_pending_password_change(db_session, client):
    org = await create_organization(db_session)
    user = await create_user(db_session, org.id, must_change_password=True)
    await db_session.commit()

    token = create_access_token({"sub": str(user.id), "organization_id": str(org.id)})

    response = await client.post(
        "/chat/messages/stream",
        json={"message": "hola"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_change_password_clears_the_flag_and_allows_reaching_chat_dependency(db_session, client):
    """No mockea el LLM real -- solo confirma que change-password apaga
    must_change_password y que, con eso apagado, la dependency de chat
    ya no devuelve 403 (llegar hasta ahi ya prueba que el gate se
    levanto; lo que pase despues con el LLM es responsabilidad de otro
    test)."""
    org = await create_organization(db_session)
    user = await create_user(db_session, org.id, must_change_password=True)
    await db_session.commit()

    token = create_access_token({"sub": str(user.id), "organization_id": str(org.id)})

    change_response = await client.post(
        "/auth/change-password",
        json={"new_password": "NuevaClaveSegura1!"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert change_response.status_code == 204

    await db_session.refresh(user)
    assert user.must_change_password is False
