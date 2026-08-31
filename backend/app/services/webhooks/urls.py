"""Deciding whether a customer-supplied URL is safe to POST to.

This is the part of outbound webhooks that is a security control rather than
a feature. Anywhere a customer can name a URL and have the server fetch it,
they have been handed a request originating from inside the deployment's
network — the classic server-side request forgery shape. The interesting
targets are not on the public internet:

* ``http://169.254.169.254/…`` — the link-local cloud metadata service,
  which on an unhardened instance hands out credentials to whoever asks.
* ``http://10.0.0.5:5432`` or ``http://localhost:6379`` — the database and
  the cache, reachable from the app and from nowhere else.
* ``http://127.0.0.1:8000/api/v1/…`` — this very API, called with whatever
  the deployment's own network position implies.

A POST of signed JSON is a limited weapon against any of those, but "the
attacker can only send JSON to your metadata service" is not a sentence
worth having to write in an incident report. So the check is a positive one:
a scheme we allow, a host that resolves to nothing private, and a port from
a short list.

Two things this deliberately does not claim.

**It is not a complete defence against DNS rebinding.** The name is resolved
here, at save time, and again by the HTTP client at send time; an attacker
controlling a name with a one-second TTL can have it answer publicly for the
first and privately for the second. Closing that properly means resolving
once and connecting to the pinned address, which needs a custom socket
factory — worth doing at scale, out of proportion to a scaffold, and called
out here so nobody assumes it is already handled.

**It is not the last line of defence.** A deployment that can reach its own
metadata service from the application container has a network policy
problem that this function cannot fix. The right answer is egress rules;
this is what stops the easy version while somebody writes them.
"""

import ipaddress
import socket
from urllib.parse import urlparse

# https only, unless the deployment opts out. An http webhook publishes
# every payload — and every signature — to anything on the path, and a
# signature does not make a plaintext body private.
ALLOWED_SCHEMES = ("https",)
INSECURE_SCHEMES = ("http", "https")

# Ports a webhook receiver plausibly listens on. An allowlist rather than a
# denylist of database ports, because the list of things worth not reaching
# is open-ended and the list of things a receiver actually uses is four
# entries long.
ALLOWED_PORTS = frozenset({80, 443, 8000, 8080, 8443})

DEFAULT_PORTS = {"http": 80, "https": 443}


class UnsafeWebhookUrlError(ValueError):
    """The URL cannot be POSTed to, with a reason fit to show the customer.

    The message is deliberately specific — "private addresses are not
    allowed" rather than "invalid URL" — because the person hitting this is
    almost always a developer testing against their laptop, not an attacker.
    Telling an attacker that 10.0.0.5 is private reveals nothing they could
    not determine from the address itself.
    """


def _resolved_addresses(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Every address `host` resolves to, or [] if it resolves to none.

    All of them, not the first: a name with an A record on the public
    internet and a AAAA record on ::1 is a bypass if only one is checked,
    and which one `getaddrinfo` returns first is not something this code
    controls.
    """
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError):
        return []

    addresses = []
    for info in infos:
        try:
            addresses.append(ipaddress.ip_address(info[4][0]))
        except ValueError:  # pragma: no cover - getaddrinfo returns valid literals
            continue
    return addresses


def _is_public(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Whether an address is on the public internet.

    `is_global` covers loopback, private ranges, link-local (including the
    metadata service at 169.254.169.254), multicast and the reserved blocks
    in one property maintained by people who track the registries. Spelling
    the ranges out by hand here would be a list that goes stale.

    IPv4-mapped IPv6 (``::ffff:127.0.0.1``) is unwrapped first, because the
    mapped form is not itself in any of those ranges — the address it maps
    to is.
    """
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_global


def validate_webhook_url(url: str, *, allow_insecure: bool = False) -> str:
    """Return `url` normalised, or raise `UnsafeWebhookUrlError`.

    `allow_insecure` is the local-development escape hatch, wired to the
    `WEBHOOK_ALLOW_INSECURE_URLS` setting. It permits plain http and hosts
    that resolve privately, which is what pointing an endpoint at
    ``http://localhost:4000`` while building a receiver requires. It is off
    by default and a production deployment that turns it on has re-opened
    everything in this module's docstring.
    """
    candidate = url.strip()
    if not candidate:
        raise UnsafeWebhookUrlError("A webhook URL is required.")

    try:
        parsed = urlparse(candidate)
    except ValueError as exc:
        raise UnsafeWebhookUrlError("That URL could not be parsed.") from exc

    schemes = INSECURE_SCHEMES if allow_insecure else ALLOWED_SCHEMES
    if parsed.scheme not in schemes:
        raise UnsafeWebhookUrlError(
            f"The URL must use {' or '.join(schemes)}, not {parsed.scheme or 'nothing'}."
        )

    if parsed.username or parsed.password:
        # Credentials in the URL would be logged with it, shown in the
        # dashboard, and stored in a column nobody treats as a secret.
        raise UnsafeWebhookUrlError("The URL must not contain credentials.")

    host = parsed.hostname
    if not host:
        raise UnsafeWebhookUrlError("The URL must include a host.")

    try:
        port = parsed.port
    except ValueError as exc:
        # urlparse defers the failure to attribute access, so "https://h:99999"
        # parses and then raises here rather than above.
        raise UnsafeWebhookUrlError("That URL's port is not valid.") from exc

    port = port if port is not None else DEFAULT_PORTS.get(parsed.scheme)
    if port not in ALLOWED_PORTS:
        raise UnsafeWebhookUrlError(
            f"Port {port} is not allowed. Use one of: "
            f"{', '.join(str(allowed) for allowed in sorted(ALLOWED_PORTS))}."
        )

    if not allow_insecure:
        addresses = _resolved_addresses(host)
        if not addresses:
            raise UnsafeWebhookUrlError(
                f"{host} does not resolve. Check the hostname and try again."
            )
        if not all(_is_public(address) for address in addresses):
            raise UnsafeWebhookUrlError(
                f"{host} resolves to a private or reserved address, which "
                "cannot be used as a webhook destination."
            )

    return candidate


__all__ = [
    "ALLOWED_PORTS",
    "ALLOWED_SCHEMES",
    "UnsafeWebhookUrlError",
    "validate_webhook_url",
]
