import pytest
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.orm import sessionmaker

from core.database import get_db, get_db_with_transaction
from main import app
from models.base import Base

# ── Sync Test DB ──
SYNC_TEST_DB_URL = "sqlite://"

test_engine = create_engine(
    SYNC_TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


def override_get_db_with_transaction():
    db = TestSessionLocal()
    try:
        with db.begin():
            yield db
    except Exception:
        raise
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[get_db_with_transaction] = override_get_db_with_transaction


@pytest.fixture(scope="session", autouse=True)
def create_tables():
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture()
def client():
    return TestClient(app)
