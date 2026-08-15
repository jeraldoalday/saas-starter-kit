import json
import logging

from app.core.logging import JsonFormatter
from app.core.request_context import get_request_id, reset_request_id, set_request_id
from app.middleware.request_id import REQUEST_ID_HEADER


def _record(**kwargs) -> logging.LogRecord:
    defaults = {
        "name": "app.test",
        "level": logging.INFO,
        "pathname": __file__,
        "lineno": 1,
        "msg": "hello",
        "args": (),
        "exc_info": None,
    }
    defaults.update(kwargs)
    return logging.LogRecord(**defaults)


def test_formatter_emits_json_with_core_fields():
    payload = json.loads(JsonFormatter().format(_record()))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["message"] == "hello"
    assert "timestamp" in payload


def test_formatter_includes_extra_fields():
    record = _record()
    record.http_status = 200
    record.duration_ms = 12.5

    payload = json.loads(JsonFormatter().format(record))

    assert payload["http_status"] == 200
    assert payload["duration_ms"] == 12.5


def test_formatter_includes_request_id_when_bound():
    token = set_request_id("req-abc")
    try:
        payload = json.loads(JsonFormatter().format(_record()))
    finally:
        reset_request_id(token)

    assert payload["request_id"] == "req-abc"


def test_request_id_is_unset_outside_a_request():
    assert get_request_id() is None


def test_response_carries_generated_request_id(client):
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER]


def test_inbound_request_id_is_preserved(client):
    response = client.get("/api/v1/health", headers={REQUEST_ID_HEADER: "trace-123"})

    assert response.headers[REQUEST_ID_HEADER] == "trace-123"
