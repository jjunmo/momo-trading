from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.config import settings

# ── Async Engine & Session ──
_connect_args = {}
if "sqlite" in settings.async_database_url:
    _connect_args["timeout"] = 30  # SQLite busy timeout (초)

async_engine = create_async_engine(
    settings.async_database_url,
    echo=False,
    connect_args=_connect_args,
)

# SQLite WAL 모드: 동시 읽기/쓰기 허용, database locked 방지
if "sqlite" in settings.async_database_url:
    @event.listens_for(async_engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

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
