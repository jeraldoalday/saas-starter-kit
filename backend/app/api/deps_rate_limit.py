import logging

from fastapi import HTTPException, Request, status

from app.api.request_info import client_ip
from app.core.config import get_settings
from app.core.rate_limit import rate_limiter

logger = logging.getLogger("app.rate_limit")
settings = get_settings()


class RateLimit:
    """Dependency enforcing `limit` requests per `window_seconds` per IP+route."""

    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window_seconds = window_seconds

    def __call__(self, request: Request) -> None:
        if not settings.rate_limit_enabled:
            return

        key = f"{request.url.path}:{client_ip(request)}"
        result = rate_limiter.hit(key, self.limit, self.window_seconds)

        if not result.allowed:
            logger.warning(
                "rate limit exceeded",
                extra={"http_path": request.url.path, "limit": self.limit},
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please try again later.",
                headers={"Retry-After": str(result.retry_after)},
            )


# Login and registration are the endpoints worth throttling: one is a
# credential-stuffing target, the other lets an anonymous caller create rows.
login_rate_limit = RateLimit(limit=10, window_seconds=60)
register_rate_limit = RateLimit(limit=5, window_seconds=60)
password_reset_rate_limit = RateLimit(limit=5, window_seconds=300)
# Ingest is authenticated and cheap, so the limit is generous: it exists to
# stop a looping client from filling the events table, not to ration normal
# use. A page load flushes one batch, not one request per event.
analytics_ingest_rate_limit = RateLimit(limit=60, window_seconds=60)
# Key creation is a session-authenticated write that mints a credential;
# a human needs a handful, a script stuck in a retry loop needs stopping.
api_key_create_rate_limit = RateLimit(limit=10, window_seconds=60)
# Registering an endpoint resolves a hostname and writes a row; the limit is
# about the resolution, which is a network call an anonymous-ish loop should
# not get to make on our behalf sixty times a minute.
webhook_endpoint_create_rate_limit = RateLimit(limit=10, window_seconds=60)
# The test ping queues a delivery to a URL the caller chose. Tighter than
# the rest: it is the one route where a caller decides both that a request
# leaves our network and roughly when.
webhook_ping_rate_limit = RateLimit(limit=6, window_seconds=60)
# Revealing a signing secret. Rate limited not because the secret is
# guessable but because a stream of reveals is what credential exfiltration
# looks like, and the audit row it writes should not be one of thousands.
webhook_secret_rate_limit = RateLimit(limit=10, window_seconds=300)
