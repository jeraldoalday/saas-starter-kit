from importlib import import_module

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.rate_limit import rate_limiter
from app.database import Base, get_db
from app.main import app
from app.services.email import InMemoryEmailSender

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def _reset_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def outbox(monkeypatch) -> InMemoryEmailSender:
    """Swap the email transport for one tests can inspect.

    Patched at each import site rather than in the factory, since the
    services bind get_email_sender at call time from their own module.
    """
    sender = InMemoryEmailSender()
    for module in ("verification_service", "password_reset_service"):
        try:
            target = import_module(f"app.services.{module}")
        except ModuleNotFoundError:
            continue
        monkeypatch.setattr(target, "get_email_sender", lambda: sender)
    return sender


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Counters are process-global, so one test's requests would otherwise
    count against the next test's budget."""
    rate_limiter.reset()
    yield
    rate_limiter.reset()


@pytest.fixture
def db_session():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    def _override_get_db():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
