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


def test_a_failing_readiness_check_alerts_once(client):
    """An orchestrator probes readiness every few seconds. The operator needs
    the first notification, not one per probe until the outage ends."""
    from app.services.alerts import (
        AlertRouter,
        InMemoryAlertSender,
        Severity,
        set_alert_router,
    )

    class _BrokenSession:
        def execute(self, *args, **kwargs):
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    def _broken_get_db():
        yield _BrokenSession()

    from app.database import get_db
    from app.main import app

    sender = InMemoryAlertSender()
    set_alert_router(
        AlertRouter(
            sender, min_severity=Severity.INFO, suppression_window_seconds=300
        )
    )
    app.dependency_overrides[get_db] = _broken_get_db

    try:
        for _ in range(3):
            assert client.get("/api/v1/health/ready").status_code == 503

        assert len(sender.sent) == 1
        assert sender.last().severity is Severity.CRITICAL
    finally:
        set_alert_router(None)
