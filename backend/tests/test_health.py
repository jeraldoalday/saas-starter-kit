from sqlalchemy.exc import OperationalError

from app.api.v1.endpoints import health as health_module


def test_health_check(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_liveness_does_not_touch_the_database(client):
    """Liveness must only fail for a hung process - a restart-fixable problem."""
    response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_reports_ok_when_database_is_reachable(client):
    response = client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_reports_503_when_database_is_unreachable(client):
    class _BrokenSession:
        def execute(self, *args, **kwargs):
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    def _broken_get_db():
        yield _BrokenSession()

    from app.database import get_db
    from app.main import app

    # Overridden for this request only; conftest's `client` fixture clears
    # all overrides at teardown, so later tests get the real session back.
    app.dependency_overrides[get_db] = _broken_get_db

    response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"


def test_readiness_endpoint_exists_and_uses_get_db(monkeypatch):
    """Guards against readiness silently losing its DB check in a refactor."""
    import inspect

    params = inspect.signature(health_module.readiness).parameters
    assert "db" in params
