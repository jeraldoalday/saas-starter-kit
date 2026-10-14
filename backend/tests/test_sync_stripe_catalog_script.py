"""The Stripe catalog sync CLI (`make sync-stripe-catalog`).

Unlike the prune scripts, this one talks to Stripe rather than the local
database, so the only things worth pinning down here are the two outcomes
an operator actually sees: a missing secret key refuses to run instead of
calling Stripe with no credentials, and a successful sync reports each
plan's price so the run's output is useful on its own.
"""

from dataclasses import dataclass

from app.scripts import sync_stripe_catalog
from app.services.stripe_catalog import SyncedPrice


@dataclass
class _FakeSettings:
    stripe_secret_key: str | None


def test_refuses_to_run_without_a_stripe_secret_key(monkeypatch, capsys):
    monkeypatch.setattr(
        sync_stripe_catalog, "get_settings", lambda: _FakeSettings(stripe_secret_key=None)
    )

    assert sync_stripe_catalog.main() == 1

    assert "STRIPE_SECRET_KEY is not set" in capsys.readouterr().err


def test_reports_each_synced_plan(monkeypatch, capsys):
    monkeypatch.setattr(
        sync_stripe_catalog, "get_settings", lambda: _FakeSettings(stripe_secret_key="sk_test_x")
    )
    monkeypatch.setattr(
        sync_stripe_catalog,
        "sync_catalog",
        lambda: [
            SyncedPrice(
                plan_key="pro_monthly",
                product_id="prod_123",
                price_id="price_123",
                created=True,
            ),
            SyncedPrice(
                plan_key="free",
                product_id="prod_456",
                price_id="price_456",
                created=False,
            ),
        ],
    )

    assert sync_stripe_catalog.main() == 0

    out = capsys.readouterr().out
    assert "pro_monthly: created price price_123 (product prod_123)" in out
    assert "free: reused existing price price_456 (product prod_456)" in out
