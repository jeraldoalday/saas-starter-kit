"""The SSRF check on customer-supplied webhook destinations.

Every case here is one a real report has been filed for somewhere: the cloud
metadata address, the loopback alias that resolves privately, the IPv4
address wearing an IPv6 costume, credentials smuggled in the authority.
"""

import ipaddress
import socket

import pytest

from app.services.webhooks.urls import (
    ALLOWED_PORTS,
    UnsafeWebhookUrlError,
    validate_webhook_url,
)


@pytest.fixture
def resolves(monkeypatch):
    """Point every hostname at addresses the test chooses.

    Real DNS in a test suite is a network dependency that fails on a plane,
    and — more to the point — the interesting cases are names that resolve
    privately, which no public resolver will hand back.
    """

    def _install(*addresses: str) -> None:
        def fake_getaddrinfo(host, port, *args, **kwargs):
            return [
                (
                    socket.AF_INET6
                    if isinstance(ipaddress.ip_address(address), ipaddress.IPv6Address)
                    else socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (address, port or 0),
                )
                for address in addresses
            ]

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    return _install


def test_a_public_https_url_is_accepted(resolves):
    resolves("93.184.216.34")

    assert validate_webhook_url("https://example.com/hooks") == "https://example.com/hooks"


def test_surrounding_whitespace_is_trimmed(resolves):
    resolves("93.184.216.34")

    assert validate_webhook_url("  https://example.com/hooks  ").endswith("/hooks")


def test_http_is_refused_by_default(resolves):
    resolves("93.184.216.34")

    with pytest.raises(UnsafeWebhookUrlError, match="must use https"):
        validate_webhook_url("http://example.com/hooks")


@pytest.mark.parametrize("scheme", ["ftp", "file", "gopher", "javascript"])
def test_only_http_schemes_are_considered(scheme, resolves):
    resolves("93.184.216.34")

    with pytest.raises(UnsafeWebhookUrlError):
        validate_webhook_url(f"{scheme}://example.com/hooks")


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",  # loopback
        "10.0.0.5",  # RFC 1918
        "172.16.4.1",
        "192.168.1.20",
        "169.254.169.254",  # the cloud metadata service
        "::1",  # IPv6 loopback
        "fd00::1",  # IPv6 unique-local
        "::ffff:127.0.0.1",  # IPv4 loopback wearing an IPv6 costume
    ],
)
def test_private_and_reserved_destinations_are_refused(address, resolves):
    resolves(address)

    with pytest.raises(UnsafeWebhookUrlError, match="private or reserved"):
        validate_webhook_url("https://internal.example.com/hooks")


def test_a_name_that_resolves_publicly_and_privately_is_refused(resolves):
    """Every answer is checked, not the first.

    A name with a public A record and a loopback AAAA record would pass a
    check that looked at whichever address `getaddrinfo` happened to return
    first — which is not something this code controls.
    """
    resolves("93.184.216.34", "::1")

    with pytest.raises(UnsafeWebhookUrlError, match="private or reserved"):
        validate_webhook_url("https://split.example.com/hooks")


def test_a_name_that_does_not_resolve_is_refused(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *args, **kwargs: (_ for _ in ()).throw(socket.gaierror())
    )

    with pytest.raises(UnsafeWebhookUrlError, match="does not resolve"):
        validate_webhook_url("https://nowhere.invalid/hooks")


def test_an_unusual_port_is_refused(resolves):
    resolves("93.184.216.34")

    with pytest.raises(UnsafeWebhookUrlError, match="not allowed"):
        validate_webhook_url("https://example.com:5432/hooks")


@pytest.mark.parametrize("port", sorted(ALLOWED_PORTS))
def test_the_allowed_ports_are_accepted(port, resolves):
    resolves("93.184.216.34")

    assert validate_webhook_url(f"https://example.com:{port}/hooks")


def test_credentials_in_the_url_are_refused(resolves):
    """They would be logged with it, shown in the dashboard, and stored in a
    column nobody treats as a secret."""
    resolves("93.184.216.34")

    with pytest.raises(UnsafeWebhookUrlError, match="credentials"):
        validate_webhook_url("https://user:password@example.com/hooks")


def test_a_url_with_no_host_is_refused():
    with pytest.raises(UnsafeWebhookUrlError):
        validate_webhook_url("https:///hooks")


def test_an_empty_url_is_refused():
    with pytest.raises(UnsafeWebhookUrlError, match="required"):
        validate_webhook_url("   ")


def test_an_out_of_range_port_is_refused():
    """urlparse defers this failure to attribute access rather than raising
    when it parses, which is the kind of thing a bare `parsed.port` misses."""
    with pytest.raises(UnsafeWebhookUrlError, match="port"):
        validate_webhook_url("https://example.com:99999/hooks")


def test_the_insecure_escape_hatch_permits_a_local_receiver(monkeypatch):
    """What building a receiver on your own machine needs.

    Also asserts the hatch skips resolution entirely: pointing at a name
    that does not resolve yet is exactly the state a half-built local setup
    is in.
    """
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError)
    )

    assert validate_webhook_url("http://localhost:8000/hooks", allow_insecure=True)


def test_the_escape_hatch_does_not_permit_a_different_scheme():
    """It relaxes http and privacy, not the shape of a URL."""
    with pytest.raises(UnsafeWebhookUrlError):
        validate_webhook_url("file:///etc/passwd", allow_insecure=True)
