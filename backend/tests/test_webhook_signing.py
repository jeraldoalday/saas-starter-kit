"""The signing scheme, from both ends.

Worth testing more carefully than most pure functions, because a signature
bug does not look like a bug: deliveries keep going out and the receiver
keeps rejecting them, and the first report comes from a customer who assumed
their own code was wrong.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.services.webhooks.signing import (
    ATTEMPT_HEADER,
    DELIVERY_HEADER,
    EVENT_HEADER,
    SECRET_PREFIX,
    SIGNATURE_HEADER,
    canonical_body,
    compute_signature,
    delivery_headers,
    generate_secret,
    parse_signature_header,
    signature_header,
    verify_signature,
)

SECRET = "whsec_test-secret-value"
BODY = '{"id":"1","type":"ping"}'
NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def test_secrets_are_prefixed_and_unique():
    first, second = generate_secret(), generate_secret()

    assert first.startswith(SECRET_PREFIX)
    assert first != second


def test_the_canonical_body_is_stable_across_key_order():
    """The property the whole scheme rests on.

    Two payloads with the same content and different insertion order must
    serialise identically, or the same event signed in two code paths
    produces two signatures.
    """
    first = canonical_body({"b": 2, "a": 1})
    second = canonical_body({"a": 1, "b": 2})

    assert first == second == '{"a":1,"b":2}'


def test_the_canonical_body_has_no_incidental_whitespace():
    """Whitespace is bytes, and bytes are what the HMAC covers."""
    assert canonical_body({"a": 1, "b": 2}) == '{"a":1,"b":2}'


def test_the_canonical_body_survives_values_json_cannot_hold():
    """A datetime in a payload must not raise on the way out.

    Payloads are assembled by call sites that may hand over a timestamp
    object; `default=str` is what keeps that from turning a delivery into an
    exception in the request that queued it.
    """
    assert '"2026-08-31 12:00:00+00:00"' in canonical_body({"at": NOW})


def test_a_signature_verifies_against_its_own_body():
    header = signature_header(SECRET, BODY, int(NOW.timestamp()))

    assert verify_signature(SECRET, BODY, header, now=NOW) is True


@pytest.mark.parametrize(
    ("secret", "body"),
    [
        ("whsec_a-different-secret", BODY),
        (SECRET, BODY + " "),
        (SECRET, '{"id":"2","type":"ping"}'),
    ],
)
def test_verification_fails_when_anything_signed_changes(secret, body):
    header = signature_header(SECRET, BODY, int(NOW.timestamp()))

    assert verify_signature(secret, body, header, now=NOW) is False


def test_a_stale_signature_is_refused():
    """A valid signature from six minutes ago is a replay, not a delivery."""
    header = signature_header(SECRET, BODY, int(NOW.timestamp()))

    later = NOW + timedelta(seconds=301)
    assert verify_signature(SECRET, BODY, header, now=later) is False
    assert verify_signature(SECRET, BODY, header, now=later, tolerance_seconds=600) is True


def test_a_signature_from_the_near_future_is_accepted():
    """Clock skew runs both ways, and a receiver ahead of us is common."""
    header = signature_header(SECRET, BODY, int((NOW + timedelta(seconds=60)).timestamp()))

    assert verify_signature(SECRET, BODY, header, now=NOW) is True


def test_rewriting_the_timestamp_does_not_rescue_a_replay():
    """The attack the timestamp-inside-the-signature design exists to stop.

    An attacker holding yesterday's request rewrites `t=` to now. If the
    timestamp were merely carried beside the signature, the freshness check
    would pass and the digest would still be the one we produced.
    """
    original = signature_header(SECRET, BODY, int((NOW - timedelta(days=1)).timestamp()))
    _, digests = parse_signature_header(original)

    forged = f"t={int(NOW.timestamp())},v1={digests[0]}"

    assert verify_signature(SECRET, BODY, forged, now=NOW) is False


@pytest.mark.parametrize(
    "header",
    ["", "garbage", "t=abc,v1=deadbeef", "v1=deadbeef", "t=1756598400", "t=0,v1=x"],
)
def test_a_malformed_header_is_refused_rather_than_raising(header):
    assert verify_signature(SECRET, BODY, header, now=NOW) is False


def test_several_signatures_are_all_considered():
    """What a rotation with an overlap window would send.

    The scheme does not currently sign twice, but a receiver reading only
    the first `v1=` would silently drop half the deliveries the day it does.
    """
    good = compute_signature(SECRET, BODY, int(NOW.timestamp()))
    header = f"t={int(NOW.timestamp())},v1=deadbeef,v1={good}"

    assert verify_signature(SECRET, BODY, header, now=NOW) is True


def test_delivery_headers_carry_everything_a_receiver_needs():
    headers = delivery_headers(
        SECRET,
        BODY,
        event_type="ping",
        delivery_id="11111111-1111-1111-1111-111111111111",
        attempt=3,
        timestamp=int(NOW.timestamp()),
    )

    assert headers["Content-Type"] == "application/json"
    assert headers[EVENT_HEADER] == "ping"
    assert headers[DELIVERY_HEADER] == "11111111-1111-1111-1111-111111111111"
    assert headers[ATTEMPT_HEADER] == "3"
    assert verify_signature(SECRET, BODY, headers[SIGNATURE_HEADER], now=NOW) is True


def test_the_delivery_id_is_stable_across_attempts():
    """What makes a receiver's handler able to be idempotent.

    "Sent and the response was lost" is indistinguishable from "never
    arrived", so the id has to identify the event rather than the attempt.
    """
    first = delivery_headers(
        SECRET, BODY, event_type="ping", delivery_id="abc", attempt=1
    )
    second = delivery_headers(
        SECRET, BODY, event_type="ping", delivery_id="abc", attempt=2
    )

    assert first[DELIVERY_HEADER] == second[DELIVERY_HEADER]
    assert first[ATTEMPT_HEADER] != second[ATTEMPT_HEADER]
