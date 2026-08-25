import pytest

from app.users.models import UserRole
from app.core.security import create_access_token 
from tests.factories import create_organization, create_user


@pytest.mark.asyncio
async def test_admin_cannot_view_user_from_another_organization(db_session, client):
    org_a = await create_organization(db_session, name="Org A")
    org_b = await create_organization(db_session, name="Org B")

    admin_a = await create_user(db_session, org_a.id, role=UserRole.ADMIN)
    member_b = await create_user(db_session, org_b.id, role=UserRole.MEMBER)
    await db_session.commit()

    token_admin_a = create_access_token({"sub": str(admin_a.id), "organization_id": str(org_a.id)})

    # Act: admin de la Org A intenta ver, por ID directo, a un usuario de la Org B
    response = await client.get(
        f"/users/{member_b.id}",
        headers={"Authorization": f"Bearer {token_admin_a}"},
    )

    # Assert: debe comportarse como si no existiera (404), nunca 403 ni 200
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_admin_cannot_change_role_of_user_from_another_organization(db_session, client):
    org_a = await create_organization(db_session, name="Org A")
    org_b = await create_organization(db_session, name="Org B")

    admin_a = await create_user(db_session, org_a.id, role=UserRole.ADMIN)
    member_b = await create_user(db_session, org_b.id, role=UserRole.MEMBER)
    await db_session.commit()

    token_admin_a = create_access_token({"sub": str(admin_a.id), "organization_id": str(org_a.id)})

    response = await client.patch(
        f"/users/{member_b.id}/role",
        json={"role": "admin"},
        headers={"Authorization": f"Bearer {token_admin_a}"},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_admin_cannot_delete_user_from_another_organization(db_session, client):
    org_a = await create_organization(db_session, name="Org A")
    org_b = await create_organization(db_session, name="Org B")

    admin_a = await create_user(db_session, org_a.id, role=UserRole.ADMIN)
    member_b = await create_user(db_session, org_b.id, role=UserRole.MEMBER)
    await db_session.commit()

    token_admin_a = create_access_token({"sub": str(admin_a.id), "organization_id": str(org_a.id)})

    response = await client.delete(
        f"/users/{member_b.id}",
        headers={"Authorization": f"Bearer {token_admin_a}"},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_users_only_returns_own_organization(db_session, client):
    org_a = await create_organization(db_session, name="Org A")
    org_b = await create_organization(db_session, name="Org B")

    admin_a = await create_user(db_session, org_a.id, role=UserRole.ADMIN)
    await create_user(db_session, org_a.id, role=UserRole.MEMBER)
    await create_user(db_session, org_b.id, role=UserRole.MEMBER)  # no debe aparecer
    await db_session.commit()

    token_admin_a = create_access_token({"sub": str(admin_a.id), "organization_id": str(org_a.id)})

    response = await client.get(
        "/users/",
        headers={"Authorization": f"Bearer {token_admin_a}"},
    )

    assert response.status_code == 200
    returned_ids = {u["id"] for u in response.json()}

    # Verificación fuerte: ningún usuario de la Org B debe estar en la respuesta
    org_b_user = await create_user(db_session, org_b.id, role=UserRole.VIEWER)
    assert str(org_b_user.id) not in returned_ids
    