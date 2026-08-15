import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.security_headers import (
    DEFAULT_SECURITY_HEADERS,
    HSTS_HEADER,
    SecurityHeadersMiddleware,
)


@pytest.mark.parametrize(("header", "value"), DEFAULT_SECURITY_HEADERS.items())
def test_default_headers_are_present(client, header, value):
    response = client.get("/api/v1/health")

    assert response.headers[header] == value


def test_headers_are_present_on_error_responses(client):
    response = client.get("/api/v1/users/me")

    assert response.status_code == 401
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_hsts_is_absent_by_default(client):
    response = client.get("/api/v1/health")

    assert HSTS_HEADER not in response.headers


def _client_with_hsts(enable_hsts: bool) -> TestClient:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware, enable_hsts=enable_hsts)

    @app.get("/ping")
    def ping() -> dict[str, str]:
        return {"ok": "true"}

    return TestClient(app)


def test_hsts_is_sent_when_enabled():
    response = _client_with_hsts(True).get("/ping")

    assert response.headers[HSTS_HEADER].startswith("max-age=")


def test_existing_header_is_not_overwritten():
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/framed")
    def framed():
        from fastapi.responses import JSONResponse

        return JSONResponse({"ok": True}, headers={"X-Frame-Options": "SAMEORIGIN"})

    response = TestClient(app).get("/framed")

    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
