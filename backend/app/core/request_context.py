"""Per-request context, carried via contextvars.

A ContextVar keeps the current request's id reachable from anywhere in the
call stack (loggers, services) without threading it through every signature,
and stays correct under concurrent requests.
"""

from contextvars import ContextVar

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def set_request_id(request_id: str) -> object:
    """Bind a request id to the current context; returns a reset token."""
    return _request_id.set(request_id)


def reset_request_id(token: object) -> None:
    _request_id.reset(token)  # type: ignore[arg-type]


def get_request_id() -> str | None:
    return _request_id.get()
