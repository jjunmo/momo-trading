from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from core.config import settings

# ── Sync Engine & Session ──
connect_args = {"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {}
engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    echo=settings.is_local,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# ── Async Engine & Session ──
async_engine = create_async_engine(
    settings.async_database_url,
    echo=settings.is_local,
)
AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False)


# ── Sync DI Generators ──
def get_db():
    """읽기 전용 sync 세션"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db_with_transaction():
    """쓰기용 sync 세션 — 자동 commit/rollback"""
    db = SessionLocal()
    try:
        with db.begin():
            yield db
    except Exception:
        raise
    finally:
        db.close()


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
