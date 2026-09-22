import uuid

import pytest

from app.gmail.errors import GmailNotConnectedError
from app.users.models import UserRole
from app.users.repository import UserRepository
from app.users.service import UserService
from app.users.tools import build_user_management_tools
from tests.factories import create_organization, create_user


def _tools_by_name(actor, service, gmail_service=None):
    return {
        tool.name: tool.handler
        for tool in build_user_management_tools(actor, service, gmail_service)
    }


@pytest.mark.asyncio
async def test_list_users_returns_only_own_organization(db_session):
    org_a = await create_organization(db_session, name="Org A")
    org_b = await create_organization(db_session, name="Org B")
    admin_a = await create_user(db_session, org_a.id, role=UserRole.ADMIN)
    await create_user(db_session, org_a.id, role=UserRole.MEMBER)
    await create_user(db_session, org_b.id, role=UserRole.MEMBER)
    await db_session.commit()

    service = UserService(UserRepository(db_session), db_session)
    tools = _tools_by_name(admin_a, service)

    result = await tools["list_users"]()

    assert len(result) == 2
    assert all(u["organization_id"] == str(org_a.id) for u in result)


@pytest.mark.asyncio
async def test_list_users_denied_for_member(db_session):
    org = await create_organization(db_session)
    member = await create_user(db_session, org.id, role=UserRole.MEMBER)
    await db_session.commit()

    service = UserService(UserRepository(db_session), db_session)
    tools = _tools_by_name(member, service)

    with pytest.raises(PermissionError):
        await tools["list_users"]()


@pytest.mark.asyncio
async def test_get_user_allows_self_regardless_of_role(db_session):
    org = await create_organization(db_session)
    viewer = await create_user(db_session, org.id, role=UserRole.VIEWER)
    await db_session.commit()

    service = UserService(UserRepository(db_session), db_session)
    tools = _tools_by_name(viewer, service)

    result = await tools["get_user"](user_id=str(viewer.id))

    assert result["id"] == str(viewer.id)


@pytest.mark.asyncio
async def test_get_user_denied_for_teammate_without_admin_role(db_session):
    org = await create_organization(db_session)
    member = await create_user(db_session, org.id, role=UserRole.MEMBER)
    other_member = await create_user(db_session, org.id, role=UserRole.MEMBER)
    await db_session.commit()

    service = UserService(UserRepository(db_session), db_session)
    tools = _tools_by_name(member, service)

    with pytest.raises(PermissionError):
        await tools["get_user"](user_id=str(other_member.id))


@pytest.mark.asyncio
async def test_get_user_from_another_organization_is_reported_as_not_found(db_session):
    org_a = await create_organization(db_session, name="Org A")
    org_b = await create_organization(db_session, name="Org B")
    admin_a = await create_user(db_session, org_a.id, role=UserRole.ADMIN)
    member_b = await create_user(db_session, org_b.id, role=UserRole.MEMBER)
    await db_session.commit()

    service = UserService(UserRepository(db_session), db_session)
    tools = _tools_by_name(admin_a, service)

    # Mismo criterio que la API REST (ver test_tenant_isolation.py): un
    # admin de otra organizacion no debe poder distinguir "no existe" de
    # "existe pero no es tuyo" -- ambos casos levantan el mismo error.
    with pytest.raises(PermissionError, match="User not found"):
        await tools["get_user"](user_id=str(member_b.id))


@pytest.mark.asyncio
async def test_update_user_allows_self_update(db_session):
    org = await create_organization(db_session)
    member = await create_user(db_session, org.id, role=UserRole.MEMBER)
    await db_session.commit()

    service = UserService(UserRepository(db_session), db_session)
    tools = _tools_by_name(member, service)

    result = await tools["update_user"](user_id=str(member.id), full_name="Nuevo Nombre")

    assert result["full_name"] == "Nuevo Nombre"


@pytest.mark.asyncio
async def test_update_user_denied_when_actor_does_not_outrank_target(db_session):
    org = await create_organization(db_session)
    member = await create_user(db_session, org.id, role=UserRole.MEMBER)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    await db_session.commit()

    service = UserService(UserRepository(db_session), db_session)
    tools = _tools_by_name(member, service)

    with pytest.raises(PermissionError):
        await tools["update_user"](user_id=str(admin.id), full_name="Hackeado")


@pytest.mark.asyncio
async def test_update_user_allowed_when_actor_outranks_target(db_session):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    member = await create_user(db_session, org.id, role=UserRole.MEMBER)
    await db_session.commit()

    service = UserService(UserRepository(db_session), db_session)
    tools = _tools_by_name(admin, service)

    result = await tools["update_user"](user_id=str(member.id), email="nuevo@test.com")

    assert result["email"] == "nuevo@test.com"


@pytest.mark.asyncio
async def test_delete_user_denied_when_actor_does_not_outrank_target(db_session):
    org = await create_organization(db_session)
    member = await create_user(db_session, org.id, role=UserRole.MEMBER)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    await db_session.commit()

    service = UserService(UserRepository(db_session), db_session)
    tools = _tools_by_name(member, service)

    with pytest.raises(PermissionError):
        await tools["delete_user"](user_id=str(admin.id))


@pytest.mark.asyncio
async def test_delete_user_allowed_when_actor_outranks_target(db_session):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    member = await create_user(db_session, org.id, role=UserRole.MEMBER)
    await db_session.commit()

    service = UserService(UserRepository(db_session), db_session)
    tools = _tools_by_name(admin, service)

    result = await tools["delete_user"](user_id=str(member.id))

    assert result == {"deleted": True, "user_id": str(member.id)}


@pytest.mark.asyncio
async def test_owner_cannot_delete_own_account(db_session):
    org = await create_organization(db_session)
    owner = await create_user(db_session, org.id, role=UserRole.OWNER)
    await db_session.commit()

    service = UserService(UserRepository(db_session), db_session)
    tools = _tools_by_name(owner, service)

    with pytest.raises(PermissionError, match="Transfer ownership"):
        await tools["delete_user"](user_id=str(owner.id))


@pytest.mark.asyncio
async def test_delete_user_from_another_organization_is_reported_as_not_found(db_session):
    org_a = await create_organization(db_session, name="Org A")
    org_b = await create_organization(db_session, name="Org B")
    admin_a = await create_user(db_session, org_a.id, role=UserRole.ADMIN)
    member_b = await create_user(db_session, org_b.id, role=UserRole.MEMBER)
    await db_session.commit()

    service = UserService(UserRepository(db_session), db_session)
    tools = _tools_by_name(admin_a, service)

    with pytest.raises(PermissionError, match="User not found"):
        await tools["delete_user"](user_id=str(member_b.id))


@pytest.mark.asyncio
async def test_change_user_role_denied_for_non_admin(db_session):
    org = await create_organization(db_session)
    member = await create_user(db_session, org.id, role=UserRole.MEMBER)
    viewer = await create_user(db_session, org.id, role=UserRole.VIEWER)
    await db_session.commit()

    service = UserService(UserRepository(db_session), db_session)
    tools = _tools_by_name(member, service)

    with pytest.raises(PermissionError, match="Admin privileges required"):
        await tools["change_user_role"](user_id=str(viewer.id), role="member")


@pytest.mark.asyncio
async def test_change_user_role_cannot_target_self(db_session):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    await db_session.commit()

    service = UserService(UserRepository(db_session), db_session)
    tools = _tools_by_name(admin, service)

    with pytest.raises(PermissionError, match="cannot change your own role"):
        await tools["change_user_role"](user_id=str(admin.id), role="member")


@pytest.mark.asyncio
async def test_change_user_role_admin_cannot_promote_to_admin(db_session):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    member = await create_user(db_session, org.id, role=UserRole.MEMBER)
    await db_session.commit()

    service = UserService(UserRepository(db_session), db_session)
    tools = _tools_by_name(admin, service)

    with pytest.raises(PermissionError, match="below ADMIN"):
        await tools["change_user_role"](user_id=str(member.id), role="admin")


@pytest.mark.asyncio
async def test_change_user_role_rejects_owner_as_target_role(db_session):
    org = await create_organization(db_session)
    owner = await create_user(db_session, org.id, role=UserRole.OWNER)
    member = await create_user(db_session, org.id, role=UserRole.MEMBER)
    await db_session.commit()

    service = UserService(UserRepository(db_session), db_session)
    tools = _tools_by_name(owner, service)

    with pytest.raises(PermissionError, match="ownership transfer"):
        await tools["change_user_role"](user_id=str(member.id), role="owner")


@pytest.mark.asyncio
async def test_change_user_role_succeeds_for_owner_over_member(db_session):
    org = await create_organization(db_session)
    owner = await create_user(db_session, org.id, role=UserRole.OWNER)
    member = await create_user(db_session, org.id, role=UserRole.MEMBER)
    await db_session.commit()

    service = UserService(UserRepository(db_session), db_session)
    tools = _tools_by_name(owner, service)

    result = await tools["change_user_role"](user_id=str(member.id), role="admin")

    assert result["role"] == "admin"


@pytest.mark.asyncio
async def test_platform_admin_can_cross_organization_boundaries(db_session):
    org_a = await create_organization(db_session, name="Org A")
    org_b = await create_organization(db_session, name="Org B")
    platform_admin = await create_user(db_session, org_a.id, role=UserRole.ADMIN)
    platform_admin.is_platform_admin = True
    member_b = await create_user(db_session, org_b.id, role=UserRole.MEMBER)
    await db_session.commit()

    service = UserService(UserRepository(db_session), db_session)
    tools = _tools_by_name(platform_admin, service)

    result = await tools["get_user"](user_id=str(member_b.id))

    assert result["id"] == str(member_b.id)


@pytest.mark.asyncio
async def test_get_user_rejects_invalid_uuid_without_raising(db_session):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    await db_session.commit()

    service = UserService(UserRepository(db_session), db_session)
    tools = _tools_by_name(admin, service)

    result = await tools["get_user"](user_id="not-a-uuid")

    assert "error" in result


@pytest.mark.asyncio
async def test_create_user_by_admin_returns_temporary_password_and_flags_must_change(db_session):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    await db_session.commit()

    service = UserService(UserRepository(db_session), db_session)
    tools = _tools_by_name(admin, service)

    result = await tools["create_user"](email="nuevo@test.com", full_name="Nuevo Usuario")

    assert result["email"] == "nuevo@test.com"
    assert result["role"] == "member"
    assert result["organization_id"] == str(org.id)
    assert isinstance(result["temporary_password"], str) and result["temporary_password"]
    assert "note" in result

    created = await service.get_by_id(uuid.UUID(result["id"]))
    assert created.must_change_password is True


@pytest.mark.asyncio
async def test_create_user_denied_for_non_admin(db_session):
    org = await create_organization(db_session)
    member = await create_user(db_session, org.id, role=UserRole.MEMBER)
    await db_session.commit()

    service = UserService(UserRepository(db_session), db_session)
    tools = _tools_by_name(member, service)

    with pytest.raises(PermissionError, match="Admin privileges required"):
        await tools["create_user"](email="nuevo@test.com", full_name="Nuevo Usuario")


@pytest.mark.asyncio
async def test_create_user_cannot_assign_owner_role(db_session):
    org = await create_organization(db_session)
    owner = await create_user(db_session, org.id, role=UserRole.OWNER)
    await db_session.commit()

    service = UserService(UserRepository(db_session), db_session)
    tools = _tools_by_name(owner, service)

    result = await tools["create_user"](email="nuevo@test.com", full_name="Nuevo", role="owner")

    assert "error" in result


@pytest.mark.asyncio
async def test_create_user_admin_cannot_create_another_admin(db_session):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    await db_session.commit()

    service = UserService(UserRepository(db_session), db_session)
    tools = _tools_by_name(admin, service)

    with pytest.raises(PermissionError, match="Admins can only create"):
        await tools["create_user"](email="nuevo@test.com", full_name="Nuevo", role="admin")


@pytest.mark.asyncio
async def test_create_user_owner_can_create_admin(db_session):
    org = await create_organization(db_session)
    owner = await create_user(db_session, org.id, role=UserRole.OWNER)
    await db_session.commit()

    service = UserService(UserRepository(db_session), db_session)
    tools = _tools_by_name(owner, service)

    result = await tools["create_user"](email="nuevoadmin@test.com", full_name="Nuevo Admin", role="admin")

    assert result["role"] == "admin"


@pytest.mark.asyncio
async def test_create_user_rejects_duplicate_email_without_raising(db_session):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    await create_user(db_session, org.id, email="taken@test.com")
    await db_session.commit()

    service = UserService(UserRepository(db_session), db_session)
    tools = _tools_by_name(admin, service)

    result = await tools["create_user"](email="taken@test.com", full_name="Nuevo")

    assert "error" in result


@pytest.mark.asyncio
async def test_create_user_rejects_invalid_role(db_session):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    await db_session.commit()

    service = UserService(UserRepository(db_session), db_session)
    tools = _tools_by_name(admin, service)

    result = await tools["create_user"](email="nuevo@test.com", full_name="Nuevo", role="superadmin")

    assert "error" in result


# -- create_user + Gmail: la contraseña se envia por correo, no se muestra ----


class FakeGmailService:
    """Doble de GmailConnectionService: solo lo que create_user llama."""

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
async def test_create_user_emails_the_password_instead_of_returning_it_when_gmail_is_connected(
    db_session,
):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    await db_session.commit()

    service = UserService(UserRepository(db_session), db_session)
    gmail = FakeGmailService()
    tools = _tools_by_name(admin, service, gmail)

    result = await tools["create_user"](email="nuevo@test.com", full_name="Nuevo Usuario")

    assert "temporary_password" not in result
    assert "nuevo@test.com" in result["note"]
    [call] = gmail.calls
    assert call["organization_id"] == org.id
    assert call["to_email"] == "nuevo@test.com"
    assert "Nuevo Usuario" in call["body"]

    created = await service.get_by_id(uuid.UUID(result["id"]))
    assert created.must_change_password is True


@pytest.mark.asyncio
async def test_create_user_falls_back_to_showing_the_password_when_email_fails(db_session):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    await db_session.commit()

    service = UserService(UserRepository(db_session), db_session)
    gmail = FakeGmailService(error=GmailNotConnectedError("no gmail"))
    tools = _tools_by_name(admin, service, gmail)

    result = await tools["create_user"](email="nuevo@test.com", full_name="Nuevo Usuario")

    assert isinstance(result["temporary_password"], str) and result["temporary_password"]
    assert gmail.calls  # se intento, pero fallo -- no bloquea la creacion


@pytest.mark.asyncio
async def test_create_user_shows_the_password_without_a_gmail_service_as_before(db_session):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    await db_session.commit()

    service = UserService(UserRepository(db_session), db_session)
    tools = _tools_by_name(admin, service)  # gmail_service=None

    result = await tools["create_user"](email="nuevo@test.com", full_name="Nuevo Usuario")

    assert isinstance(result["temporary_password"], str) and result["temporary_password"]
