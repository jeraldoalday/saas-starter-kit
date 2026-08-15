from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

# Applied to every response. The API serves JSON rather than HTML, so the CSP
# is deliberately restrictive - it only needs to cover the interactive docs and
# any accidental HTML error page, not a real UI.
DEFAULT_SECURITY_HEADERS = {
    # Stop browsers from MIME-sniffing a response into an executable type.
    "X-Content-Type-Options": "nosniff",
    # No part of a JSON API should ever be framed.
    "X-Frame-Options": "DENY",
    # Don't leak API paths to third parties via the Referer header.
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    # Opt out of powerful browser features the API has no use for.
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}

# Only meaningful over HTTPS, and harmful if sent while still on http://
# (it would pin browsers to a scheme that isn't being served yet).
HSTS_HEADER = "Strict-Transport-Security"
HSTS_VALUE = "max-age=31536000; includeSubDomains"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, enable_hsts: bool = False) -> None:
        super().__init__(app)
        self.enable_hsts = enable_hsts

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)

        for header, value in DEFAULT_SECURITY_HEADERS.items():
            # setdefault semantics: never clobber a header a route set itself.
            if header not in response.headers:
                response.headers[header] = value

        if self.enable_hsts and HSTS_HEADER not in response.headers:
            response.headers[HSTS_HEADER] = HSTS_VALUE

        return response
