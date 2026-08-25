import uuid

from app.organizations.models import Organization
from app.users.models import User, UserRole
from app.core.security import hash_password


async def create_organization(session, name: str = "Test Org") -> Organization:
    org = Organization(name=name, tax_id=str(uuid.uuid4())[:10])
    session.add(org)
    await session.flush()
    return org


async def create_user(
    session,
    organization_id: uuid.UUID,
    role: UserRole = UserRole.MEMBER,
    email: str | None = None,
) -> User:
    user = User(
        email=email or f"{uuid.uuid4()}@test.com",
        hashed_password=hash_password("Test1234!"),
        full_name="Test User",
        organization_id=organization_id,
        role=role,
    )
    session.add(user)
    await session.flush()
    return user