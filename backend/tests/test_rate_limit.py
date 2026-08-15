import pytest

from app.core.rate_limit import InMemoryRateLimiter


@pytest.fixture
def limiter() -> InMemoryRateLimiter:
    return InMemoryRateLimiter()


def test_allows_up_to_the_limit(limiter):
    results = [limiter.hit("k", limit=3, window_seconds=60) for _ in range(3)]

    assert all(result.allowed for result in results)
    assert [result.remaining for result in results] == [2, 1, 0]


def test_blocks_past_the_limit(limiter):
    for _ in range(3):
        limiter.hit("k", limit=3, window_seconds=60)

    result = limiter.hit("k", limit=3, window_seconds=60)

    assert result.allowed is False
    assert result.remaining == 0
    assert result.retry_after >= 1


def test_keys_are_tracked_independently(limiter):
    for _ in range(3):
        limiter.hit("a", limit=3, window_seconds=60)

    assert limiter.hit("b", limit=3, window_seconds=60).allowed is True


def test_window_resets_after_it_lapses(limiter):
    # A zero-length window has always elapsed, so the counter restarts.
    for _ in range(5):
        limiter.hit("k", limit=1, window_seconds=0)

    assert limiter.hit("k", limit=1, window_seconds=0).allowed is True


def test_login_endpoint_returns_429_when_flooded(client):
    payload = {"email": "flood@example.com", "password": "wrong-password"}

    statuses = [
        client.post("/api/v1/auth/login", json=payload).status_code for _ in range(12)
    ]

    assert 429 in statuses
    # The limit is 10/min, so the first ten are merely bad credentials.
    assert statuses[:10] == [401] * 10


def test_rate_limited_response_carries_retry_after(client):
    payload = {"email": "flood2@example.com", "password": "wrong-password"}

    response = None
    for _ in range(12):
        response = client.post("/api/v1/auth/login", json=payload)
        if response.status_code == 429:
            break

    assert response is not None
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) >= 1


def test_register_endpoint_is_rate_limited(client):
    statuses = []
    for i in range(8):
        statuses.append(
            client.post(
                "/api/v1/auth/register",
                json={"email": f"u{i}@example.com", "password": "s3cret-password"},
            ).status_code
        )

    assert 429 in statuses
