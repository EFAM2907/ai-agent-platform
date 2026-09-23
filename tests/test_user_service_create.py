"""UserService.create() -- flujo de invitacion: quien invita nunca
elige la contraseña de otro usuario, siempre se genera una temporal
(ver app.core.security.generate_temporary_password) y la cuenta queda
con must_change_password=True hasta que pase por
POST /auth/change-password. Sin cobertura previa a este cambio (el
create() anterior tomaba `password` en UserCreate) -- este archivo es
nuevo.
"""

import pytest

from app.core.security import verify_password
from app.users.exceptions import UserAlreadyExistsError
from app.users.models import UserRole
from app.users.repository import UserRepository
from app.users.schemas import UserCreate
from app.users.service import UserService
from tests.factories import create_organization, create_user


@pytest.mark.asyncio
async def test_create_returns_user_and_plaintext_temporary_password(db_session):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    await db_session.commit()

    service = UserService(UserRepository(db_session), db_session)
    user, temporary_password = await service.create(
        admin, UserCreate(email="new@test.com", full_name="Nuevo Usuario")
    )

    assert user.email == "new@test.com"
    assert isinstance(temporary_password, str)
    assert len(temporary_password) > 0


@pytest.mark.asyncio
async def test_created_user_must_change_password_and_belongs_to_admins_org(db_session):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    await db_session.commit()

    service = UserService(UserRepository(db_session), db_session)
    user, _ = await service.create(
        admin, UserCreate(email="new2@test.com", full_name="Nuevo Usuario")
    )

    assert user.must_change_password is True
    assert user.organization_id == org.id


@pytest.mark.asyncio
async def test_temporary_password_is_hashed_not_stored_in_plaintext(db_session):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    await db_session.commit()

    service = UserService(UserRepository(db_session), db_session)
    user, temporary_password = await service.create(
        admin, UserCreate(email="new3@test.com", full_name="Nuevo Usuario")
    )

    assert user.hashed_password != temporary_password
    assert verify_password(temporary_password, user.hashed_password)


@pytest.mark.asyncio
async def test_create_defaults_to_member_role(db_session):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    await db_session.commit()

    service = UserService(UserRepository(db_session), db_session)
    user, _ = await service.create(
        admin, UserCreate(email="new4@test.com", full_name="Nuevo Usuario")
    )

    assert user.role == UserRole.MEMBER


@pytest.mark.asyncio
async def test_create_rejects_duplicate_email(db_session):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    existing = await create_user(db_session, org.id, email="taken@test.com")
    await db_session.commit()

    service = UserService(UserRepository(db_session), db_session)

    with pytest.raises(UserAlreadyExistsError):
        await service.create(admin, UserCreate(email="taken@test.com", full_name="Otro"))


@pytest.mark.asyncio
async def test_two_invited_users_get_different_temporary_passwords(db_session):
    org = await create_organization(db_session)
    admin = await create_user(db_session, org.id, role=UserRole.ADMIN)
    await db_session.commit()

    service = UserService(UserRepository(db_session), db_session)
    _, password_a = await service.create(
        admin, UserCreate(email="a@test.com", full_name="A")
    )
    _, password_b = await service.create(
        admin, UserCreate(email="b@test.com", full_name="B")
    )

    assert password_a != password_b
