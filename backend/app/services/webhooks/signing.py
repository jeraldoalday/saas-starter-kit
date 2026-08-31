"""Signing outbound webhooks, and verifying one on the way back in.

A webhook is an unauthenticated POST arriving at a URL that is, by
construction, public. Without a signature the receiver's only options are to
trust anything that arrives — which means anyone who learns the URL can
forge a subscription upgrade — or to call back into our API to confirm every
event, which turns one request into two and only works while we are up.

So each request carries an HMAC of its own body under a secret only the two
parties hold:

    X-Webhook-Signature: t=1756598400,v1=6f1a…

Three details are worth stating, because each of them is a real attack if
got wrong.

**The timestamp is inside the signed string, not beside it.** The signed
material is ``f"{timestamp}.{body}"``. If the timestamp were merely a header
sent alongside the signature, an attacker replaying yesterday's request
could rewrite it to now and the signature would still verify — the freshness
check would be checking a value nobody signed.

**Verification is constant-time.** `hmac.compare_digest`, not `==`. A
receiver written with `==` leaks the correct digest one byte at a time to
anyone who can measure the difference, and this module is copied as much as
it is called: `verify_signature` exists here so a customer has a correct
implementation to port rather than a scheme to reimplement from prose.

**The scheme is versioned in the header.** The `v1=` prefix is what allows a
second algorithm to be added later by sending both, letting receivers
migrate at their own pace. A bare hex string would make that a flag day for
every customer at once.
"""

import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime
from typing import Any

# Distinct from the API key prefix (`sk_live_`) on purpose: the two secrets
# do opposite jobs, and a value pasted into the wrong configuration field
# should be recognisable as the wrong kind of thing at a glance. The
# spelling matches Stripe's convention, which is what a developer wiring up
# a receiver will already have a mental model for.
SECRET_PREFIX = "whsec_"

# 32 bytes from `secrets` — the same 256 bits the API keys use, and for the
# same reason: nothing about an HMAC key needs to be memorable, so there is
# no argument for making it shorter.
SECRET_BYTES = 32

#: The header carrying the signature, and the one carrying the event id.
#: Named as constants because the delivery worker sets them and the tests
#: assert on them, and a header name that differs by a hyphen between those
#: two places is a bug nobody sees until a customer reports it.
SIGNATURE_HEADER = "X-Webhook-Signature"
EVENT_HEADER = "X-Webhook-Event"
DELIVERY_HEADER = "X-Webhook-Delivery"
ATTEMPT_HEADER = "X-Webhook-Attempt"

#: How far a request's timestamp may be from the receiver's clock before
#: verification refuses it. Five minutes is the width of the replay window
#: this leaves open, traded against the clock skew a receiver with no NTP
#: will have. Both failure modes are real; five minutes is where everyone
#: who has published this scheme has landed.
DEFAULT_TOLERANCE_SECONDS = 300

_SIGNATURE_VERSION = "v1"


def generate_secret() -> str:
    """Mint a signing secret.

    Returned in full and stored in full: see `WebhookEndpoint` for why this
    one cannot be a digest.
    """
    return f"{SECRET_PREFIX}{secrets.token_urlsafe(SECRET_BYTES)}"


def canonical_body(payload: dict[str, Any]) -> str:
    """Serialise a payload to the exact bytes that will be signed and sent.

    One function, called by both the signer and the sender, because the
    signature covers the body *as transmitted*. Serialising twice — once to
    sign, once to send — invites the two to differ by a space or a key
    order, and the receiver's only symptom is a signature that never
    verifies against a body that looks perfectly correct.

    `sort_keys` is what makes the output reproducible across processes:
    Python dicts preserve insertion order, so a payload assembled by a
    different code path would otherwise serialise differently and produce a
    different signature for the same event.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def compute_signature(secret: str, body: str, timestamp: int) -> str:
    """The hex HMAC-SHA256 of `timestamp.body` under `secret`."""
    signed = f"{timestamp}.{body}".encode()
    return hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()


def signature_header(secret: str, body: str, timestamp: int | None = None) -> str:
    """The full `X-Webhook-Signature` value for one request."""
    timestamp = timestamp if timestamp is not None else int(datetime.now(UTC).timestamp())
    digest = compute_signature(secret, body, timestamp)
    return f"t={timestamp},{_SIGNATURE_VERSION}={digest}"


def parse_signature_header(header: str) -> tuple[int, list[str]]:
    """Split a header into its timestamp and its candidate digests.

    Returns `(timestamp, digests)`; a malformed header yields `(0, [])`
    rather than raising, because every caller of this is deciding whether to
    trust a request from the open internet, and "unparseable" and "wrong"
    lead to the same refusal. Raising would only give each caller a chance
    to forget the try block.

    Several `v1=` parts are tolerated and all are returned: that is how a
    secret rotation with an overlap window is delivered — both signatures on
    one request — and a receiver that only reads the first would drop half
    of them.
    """
    timestamp = 0
    digests: list[str] = []

    for part in header.split(","):
        key, separator, value = part.strip().partition("=")
        if not separator:
            continue
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError:
                return 0, []
        elif key == _SIGNATURE_VERSION:
            digests.append(value)

    return timestamp, digests


def verify_signature(
    secret: str,
    body: str,
    header: str,
    *,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
    now: datetime | None = None,
) -> bool:
    """Whether `header` is a valid, fresh signature of `body` under `secret`.

    This is the receiver's half of the scheme. It lives in this codebase
    because the scaffold's own integration tests need it and because it is
    the reference a customer ports — not because anything in the request
    path calls it. Inbound Stripe webhooks are verified by Stripe's library
    in `billing_service`, which implements this same shape.

    Both halves of the check matter and neither is sufficient alone: a valid
    signature on a request from last week is a replay, and a fresh timestamp
    on an unsigned body is a forgery.
    """
    timestamp, digests = parse_signature_header(header)
    if timestamp <= 0 or not digests:
        return False

    now = now or datetime.now(UTC)
    if abs(int(now.timestamp()) - timestamp) > tolerance_seconds:
        return False

    expected = compute_signature(secret, body, timestamp)
    # Every candidate is compared, and compared in constant time. Bailing
    # out early on the first match would be fine; bailing out on the first
    # *mismatch* with `==` is the timing leak this function exists to avoid
    # teaching.
    return any(hmac.compare_digest(expected, digest) for digest in digests)


def delivery_headers(
    secret: str,
    body: str,
    *,
    event_type: str,
    delivery_id: str,
    attempt: int,
    timestamp: int | None = None,
) -> dict[str, str]:
    """Every header one delivery attempt carries.

    The delivery id is stable across retries on purpose: it is what lets a
    receiver make its handler idempotent, which it has to be, because "we
    sent it and the response was lost" is indistinguishable from "we sent it
    and it never arrived". The attempt number changes, and is there for the
    receiver's logs rather than for its logic.
    """
    return {
        "Content-Type": "application/json",
        SIGNATURE_HEADER: signature_header(secret, body, timestamp),
        EVENT_HEADER: event_type,
        DELIVERY_HEADER: delivery_id,
        ATTEMPT_HEADER: str(attempt),
    }


__all__ = [
    "ATTEMPT_HEADER",
    "DEFAULT_TOLERANCE_SECONDS",
    "DELIVERY_HEADER",
    "EVENT_HEADER",
    "SECRET_PREFIX",
    "SIGNATURE_HEADER",
    "canonical_body",
    "compute_signature",
    "delivery_headers",
    "generate_secret",
    "parse_signature_header",
    "signature_header",
    "verify_signature",
]
