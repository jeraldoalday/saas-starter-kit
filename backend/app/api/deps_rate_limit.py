import logging

from fastapi import HTTPException, Request, status

from app.core.config import get_settings
from app.core.rate_limit import rate_limiter

logger = logging.getLogger("app.rate_limit")
settings = get_settings()


def _client_ip(request: Request) -> str:
    """Best-effort client IP.

    X-Forwarded-For is only trustworthy when a proxy you control appends to it;
    it is client-supplied otherwise. TRUST_PROXY_HEADERS gates that, so the
    default deployment can't be bypassed by spoofing the header.
    """
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimit:
    """Dependency enforcing `limit` requests per `window_seconds` per IP+route."""

    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window_seconds = window_seconds

    def __call__(self, request: Request) -> None:
        if not settings.rate_limit_enabled:
            return

        key = f"{request.url.path}:{_client_ip(request)}"
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
