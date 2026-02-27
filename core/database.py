from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.config import settings

# ── Async Engine & Session ──
async_engine = create_async_engine(
    settings.async_database_url,
    echo=settings.is_local,
)
AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False)


# ── Async DI Generators ──
async def get_async_db():
    """읽기 전용 async 세션"""
    async with AsyncSessionLocal() as session:
        yield session


async def get_async_db_with_transaction():
    """쓰기용 async 세션 — 자동 commit/rollback"""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            yield session
