"""`post_json`: the real urllib transport behind outbound webhooks.

Every other webhook test substitutes a fake `Transport` so the queue tests
read as decisions rather than sockets. This file is the one place that talks
to an actual listening socket, so the HTTP-handling branches `post_json`
itself contains -- a 2xx, a receiver-side error with a body worth quoting,
and a connection that never completes -- run for real at least once.
"""

import http.server
import threading
from contextlib import contextmanager

from app.services.webhooks.dispatch import post_json


@contextmanager
def _server(handler_cls):
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}/hook"
    finally:
        httpd.shutdown()
        thread.join()


class _Ok(http.server.BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - required name by http.server
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):  # silence the default stderr logging
        pass


class _ServerError(http.server.BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        body = b"receiver is on fire\nsecond line is dropped"
        self.send_response(500)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # noqa: D401
        pass


def test_a_2xx_response_is_success():
    with _server(_Ok) as url:
        result = post_json(url, "{}", {"Content-Type": "application/json"}, 2.0)
    assert result.ok is True
    assert result.status_code == 204
    assert result.error is None


def test_an_error_response_carries_the_first_line_of_the_body():
    with _server(_ServerError) as url:
        result = post_json(url, "{}", {"Content-Type": "application/json"}, 2.0)
    assert result.ok is False
    assert result.status_code == 500
    assert result.error == "HTTP 500: receiver is on fire"


def test_a_refused_connection_has_no_status_code():
    # Nothing is listening on this port, so the connection itself fails
    # before any HTTP status exists to report.
    result = post_json(
        "http://127.0.0.1:1/hook", "{}", {"Content-Type": "application/json"}, 2.0
    )
    assert result.ok is False
    assert result.status_code is None
    assert result.error
