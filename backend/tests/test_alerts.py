"""Alert routing: the severity floor, suppression, transports, and wiring.

The property every one of these guards is that alerting must not be able to
make an incident worse — not by raising, not by blocking, and not by
flooding the channel that the operator is trying to read.
"""

import json
from urllib.error import URLError

import pytest

from app.core.rate_limit import rate_limiter
from app.models.role import Role
from app.models.user import User
from app.services import billing_service
from app.services.alerts import (
    Alert,
    AlertRouter,
    InMemoryAlertSender,
    LoggingAlertSender,
    Severity,
    WebhookAlertSender,
    get_alert_router,
    notify,
    set_alert_router,
    severity_rank,
)
from app.services.alerts import factory as alert_factory


@pytest.fixture
def sender() -> InMemoryAlertSender:
    return InMemoryAlertSender()


@pytest.fixture
def router(sender) -> AlertRouter:
    """Install a router tests can inspect, and take it out again after."""
    installed = AlertRouter(sender, min_severity=Severity.INFO)
    set_alert_router(installed)
    yield installed
    set_alert_router(None)


def _alert(**overrides) -> Alert:
    defaults = {
        "title": "Something happened",
        "message": "Details here",
        "severity": Severity.WARNING,
    }
    return Alert(**{**defaults, **overrides})


# --- severity -------------------------------------------------------------


def test_severity_ranks_ascend():
    assert severity_rank(Severity.INFO) < severity_rank(Severity.WARNING)
    assert severity_rank(Severity.WARNING) < severity_rank(Severity.CRITICAL)


def test_an_unknown_severity_is_never_dropped():
    """Unlike roles, where an unknown value must deny; a mislabelled alert
    should still page rather than vanish."""
    assert severity_rank("catastrophic") >= severity_rank(Severity.CRITICAL)


def test_the_severity_floor_filters_quiet_alerts(sender):
    router = AlertRouter(sender, min_severity=Severity.WARNING)

    assert router.dispatch(_alert(severity=Severity.INFO)) is False
    assert router.dispatch(_alert(severity=Severity.WARNING)) is True
    assert router.dispatch(_alert(severity=Severity.CRITICAL, key="other")) is True
    assert len(sender.sent) == 2


# --- suppression ----------------------------------------------------------


def test_repeat_alerts_are_suppressed_within_the_window(sender):
    router = AlertRouter(
        sender, min_severity=Severity.INFO, suppression_window_seconds=300
    )
    alert = _alert(key="health.database_unreachable")

    first = router.dispatch(alert, now=1000.0)
    repeat = router.dispatch(alert, now=1100.0)
    later = router.dispatch(alert, now=1400.0)

    assert (first, repeat, later) == (True, False, True)
    assert len(sender.sent) == 2


def test_suppression_is_per_key(sender):
    router = AlertRouter(
        sender, min_severity=Severity.INFO, suppression_window_seconds=300
    )

    router.dispatch(_alert(key="billing.payment_failed:sub_1"), now=1000.0)
    delivered = router.dispatch(_alert(key="billing.payment_failed:sub_2"), now=1001.0)

    assert delivered is True


def test_alerts_without_a_key_group_on_title_and_severity(sender):
    router = AlertRouter(
        sender, min_severity=Severity.INFO, suppression_window_seconds=300
    )

    router.dispatch(_alert(title="Disk full"), now=1000.0)
    same = router.dispatch(_alert(title="Disk full"), now=1010.0)
    different = router.dispatch(_alert(title="Queue backed up"), now=1010.0)

    assert (same, different) == (False, True)


def test_suppression_can_be_switched_off(sender):
    router = AlertRouter(
        sender, min_severity=Severity.INFO, suppression_window_seconds=0
    )

    assert router.dispatch(_alert(key="k"), now=1.0) is True
    assert router.dispatch(_alert(key="k"), now=1.0) is True


def test_reset_clears_suppression_state(sender):
    router = AlertRouter(
        sender, min_severity=Severity.INFO, suppression_window_seconds=300
    )
    router.dispatch(_alert(key="k"), now=1000.0)

    router.reset()

    assert router.dispatch(_alert(key="k"), now=1001.0) is True


# --- transports -----------------------------------------------------------


def test_a_transport_that_raises_does_not_propagate():
    class BrokenSender:
        def send(self, alert):
            raise RuntimeError("pager is on fire")

    router = AlertRouter(BrokenSender(), min_severity=Severity.INFO)

    assert router.dispatch(_alert()) is False


def test_the_logging_transport_always_accepts(caplog):
    with caplog.at_level("WARNING", logger="app.alerts"):
        delivered = LoggingAlertSender().send(_alert(title="Readiness failing"))

    assert delivered is True
    assert "Readiness failing" in caplog.text


def test_the_webhook_transport_posts_a_slack_shaped_payload(monkeypatch):
    captured = {}

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def _fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    transport = WebhookAlertSender(
        "https://hooks.example.com/abc", timeout_seconds=2.5, environment="staging"
    )

    delivered = transport.send(_alert(severity=Severity.CRITICAL, title="DB down"))

    assert delivered is True
    assert captured["url"] == "https://hooks.example.com/abc"
    assert captured["timeout"] == 2.5
    assert "[staging]" in captured["body"]["text"]
    assert "CRITICAL" in captured["body"]["text"]
    assert captured["body"]["severity"] == "critical"
    assert captured["body"]["title"] == "DB down"


def test_the_webhook_transport_swallows_delivery_failures(monkeypatch):
    def _fake_urlopen(request, timeout=None):
        raise URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

    assert WebhookAlertSender("https://hooks.example.com/abc").send(_alert()) is False


def test_the_webhook_transport_refuses_an_empty_url():
    assert WebhookAlertSender("").send(_alert()) is False


def test_a_non_2xx_response_is_not_a_delivery(monkeypatch):
    class _Response:
        status = 500

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: _Response())

    assert WebhookAlertSender("https://hooks.example.com/abc").send(_alert()) is False


# --- configuration --------------------------------------------------------


def test_the_console_backend_resolves_to_the_log_transport(monkeypatch):
    """The other spelling of the same transport, kept working for anyone who
    configured it before the two alerting modules were consolidated."""
    alert_factory.get_alert_sender.cache_clear()
    monkeypatch.setattr(alert_factory.get_settings(), "alert_backend", "console")

    assert isinstance(alert_factory.get_alert_sender(), LoggingAlertSender)
    alert_factory.get_alert_sender.cache_clear()


def test_the_default_transport_is_the_log(monkeypatch):
    alert_factory.get_alert_sender.cache_clear()
    monkeypatch.setattr(alert_factory.get_settings(), "alert_backend", "log")

    assert isinstance(alert_factory.get_alert_sender(), LoggingAlertSender)
    alert_factory.get_alert_sender.cache_clear()


def test_the_webhook_transport_needs_a_url(monkeypatch):
    """Selecting "webhook" without a URL falls back rather than dropping
    alerts into a transport that can never deliver them."""
    settings = alert_factory.get_settings()
    alert_factory.get_alert_sender.cache_clear()
    monkeypatch.setattr(settings, "alert_backend", "webhook")
    monkeypatch.setattr(settings, "alert_webhook_url", "")

    assert isinstance(alert_factory.get_alert_sender(), LoggingAlertSender)

    monkeypatch.setattr(settings, "alert_webhook_url", "https://hooks.example.com/abc")
    alert_factory.get_alert_sender.cache_clear()
    assert isinstance(alert_factory.get_alert_sender(), WebhookAlertSender)
    alert_factory.get_alert_sender.cache_clear()


def test_the_process_router_is_built_once():
    set_alert_router(None)
    try:
        assert get_alert_router() is get_alert_router()
    finally:
        set_alert_router(None)


# --- wiring ---------------------------------------------------------------


def test_notify_reaches_the_installed_router(router, sender):
    delivered = notify("Title", "Message", severity=Severity.CRITICAL, key="k", extra=1)

    assert delivered is True
    assert sender.last().context == {"extra": 1}


def test_a_rejected_stripe_signature_raises_an_alert(db_session, router, sender, monkeypatch):
    monkeypatch.setattr(billing_service.settings, "stripe_webhook_secret", "whsec_test")

    def _reject(*_args, **_kwargs):
        raise ValueError("bad payload")

    monkeypatch.setattr(billing_service.stripe.Webhook, "construct_event", _reject)

    with pytest.raises(billing_service.InvalidWebhookSignatureError):
        billing_service.handle_webhook_event(db_session, b"{}", "sig")

    assert "Stripe webhook signature rejected" in sender.titles()


def test_a_missing_webhook_secret_raises_a_critical_alert(
    db_session, router, sender, monkeypatch
):
    monkeypatch.setattr(billing_service.settings, "stripe_webhook_secret", "")

    with pytest.raises(billing_service.InvalidWebhookSignatureError):
        billing_service.handle_webhook_event(db_session, b"{}", "sig")

    assert sender.last().severity is Severity.CRITICAL


def test_the_test_endpoint_is_owner_only(client, router):
    rate_limiter.reset()
    client.post(
        "/api/v1/auth/register",
        json={"email": "member@example.com", "password": "s3cret-password"},
    )
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "member@example.com", "password": "s3cret-password"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = client.post("/api/v1/admin/alerts/test", json={}, headers=headers)

    assert response.status_code == 403


def test_the_test_endpoint_pushes_a_real_alert(client, db_session, router, sender):
    rate_limiter.reset()
    client.post(
        "/api/v1/auth/register",
        json={"email": "owner@example.com", "password": "s3cret-password"},
    )
    owner = db_session.query(User).filter(User.email == "owner@example.com").one()
    owner.role = Role.OWNER.value
    db_session.commit()
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "s3cret-password"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = client.post(
        "/api/v1/admin/alerts/test", json={"message": "ping"}, headers=headers
    )

    assert response.status_code == 200
    assert response.json() == {"delivered": True, "backend": "InMemoryAlertSender"}
    assert sender.last().message == "ping"


def test_the_test_endpoint_ignores_suppression(client, db_session, router, sender):
    """Two tests a minute apart must both arrive, or the second one tells
    the operator nothing."""
    rate_limiter.reset()
    client.post(
        "/api/v1/auth/register",
        json={"email": "owner2@example.com", "password": "s3cret-password"},
    )
    owner = db_session.query(User).filter(User.email == "owner2@example.com").one()
    owner.role = Role.OWNER.value
    db_session.commit()
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "owner2@example.com", "password": "s3cret-password"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    client.post("/api/v1/admin/alerts/test", json={}, headers=headers)
    client.post("/api/v1/admin/alerts/test", json={}, headers=headers)

    assert len(sender.sent) == 2


def test_config_severities_match_the_enum():
    """app/core/config.py spells the severities out as strings to avoid an
    import cycle. This is what keeps that copy honest."""
    from app.core.config import ALERT_SEVERITIES

    assert set(ALERT_SEVERITIES) == {severity.value for severity in Severity}
