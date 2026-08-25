import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.main import app
from app.core.database import get_db

TEST_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5433/ai_agent_platform_test"


@pytest_asyncio.fixture
async def db_session():
    # El engine se crea DENTRO del fixture (no a nivel de módulo), para que quede
    # atado al event loop de ESTE test específico, no al del primer test que corrió.
    # NullPool evita que se reutilicen conexiones entre event loops distintos.
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)

    connection = await engine.connect()
    transaction = await connection.begin()
    TestSessionLocal = async_sessionmaker(bind=connection, expire_on_commit=False)
    session = TestSessionLocal()

    yield session

    await session.close()
    await transaction.rollback()
    await connection.close()
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db_session):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()