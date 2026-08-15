from app.core.billing_plans import BillingPlan
from app.services import stripe_catalog


class _FakePrice:
    def __init__(self, id: str, product: str):
        self.id = id
        self.product = product


class _FakeListResult:
    def __init__(self, data):
        self.data = data


PLAN = BillingPlan(
    key="pro_monthly",
    product_name="Pro",
    product_description="Full platform access, billed monthly.",
    lookup_key="pro_monthly",
    unit_amount=2900,
    currency="usd",
    interval="month",
)


def test_sync_catalog_reuses_existing_price(monkeypatch):
    existing = _FakePrice(id="price_existing", product="prod_existing")
    monkeypatch.setattr(
        stripe_catalog.stripe.Price, "list", lambda **kwargs: _FakeListResult([existing])
    )

    def _fail_create(**kwargs):
        raise AssertionError("should not create a new product when one already exists")

    monkeypatch.setattr(stripe_catalog.stripe.Product, "create", _fail_create)
    monkeypatch.setattr(stripe_catalog.stripe.Price, "create", _fail_create)

    [result] = stripe_catalog.sync_catalog([PLAN])

    assert result.created is False
    assert result.plan_key == "pro_monthly"
    assert result.price_id == "price_existing"
    assert result.product_id == "prod_existing"


def test_sync_catalog_creates_missing_plan(monkeypatch):
    monkeypatch.setattr(stripe_catalog.stripe.Price, "list", lambda **kwargs: _FakeListResult([]))

    created_products = []
    created_prices = []

    class _FakeProduct:
        id = "prod_new"

    class _FakePriceObj:
        id = "price_new"

    def _create_product(**kwargs):
        created_products.append(kwargs)
        return _FakeProduct()

    def _create_price(**kwargs):
        created_prices.append(kwargs)
        return _FakePriceObj()

    monkeypatch.setattr(stripe_catalog.stripe.Product, "create", _create_product)
    monkeypatch.setattr(stripe_catalog.stripe.Price, "create", _create_price)

    [result] = stripe_catalog.sync_catalog([PLAN])

    assert result.created is True
    assert result.price_id == "price_new"
    assert result.product_id == "prod_new"
    assert created_products[0]["metadata"] == {"plan_key": "pro_monthly"}
    assert created_prices[0]["product"] == "prod_new"
    assert created_prices[0]["lookup_key"] == "pro_monthly"
    assert created_prices[0]["recurring"] == {"interval": "month"}


def test_get_price_id_returns_none_when_missing(monkeypatch):
    monkeypatch.setattr(stripe_catalog.stripe.Price, "list", lambda **kwargs: _FakeListResult([]))

    assert stripe_catalog.get_price_id("does_not_exist") is None


def test_get_price_id_returns_existing_price(monkeypatch):
    existing = _FakePrice(id="price_existing", product="prod_existing")
    monkeypatch.setattr(
        stripe_catalog.stripe.Price, "list", lambda **kwargs: _FakeListResult([existing])
    )

    assert stripe_catalog.get_price_id("pro_monthly") == "price_existing"
