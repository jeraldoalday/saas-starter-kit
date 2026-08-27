"""What can be learned about the caller from the request itself.

Both of these are client-supplied and neither is trustworthy on its own.
They are shared here rather than restated per module so the rules about
*how far* to trust them are written down once: the rate limiter and the
session list draw the same conclusions from the same headers, and a change
to proxy handling lands in one place.
"""

from fastapi import Request

from app.core.config import get_settings


def client_ip(request: Request) -> str:
    """Best-effort client IP.

    X-Forwarded-For is only trustworthy when a proxy you control appends to
    it; it is client-supplied otherwise. TRUST_PROXY_HEADERS gates that, so
    the default deployment can't be bypassed by spoofing the header.
    """
    if get_settings().trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def user_agent(request: Request) -> str | None:
    """The caller's self-reported User-Agent, or None if it sent none.

    Never validated and never trusted — it exists so someone reading their
    own session list can tell "my laptop" from "my phone". A client that
    lies about it only misleads itself.
    """
    return request.headers.get("user-agent")


def request_origin(request: Request) -> dict[str, str | None]:
    """Both of the above, shaped for splatting into an audit record.

    A convenience rather than a new idea: the audit trail wants the pair on
    every call, and spelling out two keyword arguments at a dozen call sites
    is how one of them eventually gets forgotten.
    """
    return {"ip_address": client_ip(request), "user_agent": user_agent(request)}
