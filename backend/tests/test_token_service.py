"""Unit-level cover for one-time token issuance, redemption and pruning.

The flows that use these tokens are covered end to end elsewhere
(tests/test_verification.py, tests/test_password_reset.py). This file stays
at the service so the two properties that matter on their own — a token
works exactly once, and a dead token eventually leaves the table — are
asserted directly rather than inferred from an email arriving.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.security import hash_password
from app.models.token import OneTimeToken, TokenPurpose
from app.models.user import User
from app.services.token_service import (
    consume_token,
    hash_token,
    issue_token,
    purge_expired,
)


@pytest.fixture
def user(db_session) -> User:
    account = User(
        email="tokens@example.com",
        hashed_password=hash_password("s3cret-password"),
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def _rows(db_session) -> list[OneTimeToken]:
    return db_session.query(OneTimeToken).all()


def test_issued_token_is_stored_only_as_a_hash(db_session, user):
    raw = issue_token(db_session, user, TokenPurpose.PASSWORD_RESET)

    stored = _rows(db_session)
    assert len(stored) == 1
    assert stored[0].token_hash == hash_token(raw)
    # The raw value must not be recoverable from the row.
    assert raw not in stored[0].token_hash


def test_issuing_replaces_the_previous_token_of_the_same_purpose(db_session, user):
    first = issue_token(db_session, user, TokenPurpose.PASSWORD_RESET)
    second = issue_token(db_session, user, TokenPurpose.PASSWORD_RESET)

    assert consume_token(db_session, first, TokenPurpose.PASSWORD_RESET) is None
    assert consume_token(db_session, second, TokenPurpose.PASSWORD_RESET) is not None


def test_issuing_leaves_other_purposes_alone(db_session, user):
    reset = issue_token(db_session, user, TokenPurpose.PASSWORD_RESET)
    issue_token(db_session, user, TokenPurpose.EMAIL_VERIFICATION)

    # Verification and reset are independent flows; minting one must not
    # invalidate a link the user is part-way through using for the other.
    assert consume_token(db_session, reset, TokenPurpose.PASSWORD_RESET) is not None


def test_a_token_cannot_be_redeemed_twice(db_session, user):
    raw = issue_token(db_session, user, TokenPurpose.PASSWORD_RESET)

    assert consume_token(db_session, raw, TokenPurpose.PASSWORD_RESET) is not None
    assert consume_token(db_session, raw, TokenPurpose.PASSWORD_RESET) is None


def test_a_token_is_not_valid_for_a_different_purpose(db_session, user):
    raw = issue_token(db_session, user, TokenPurpose.PASSWORD_RESET)

    # A reset link must not double as a verification link: the two carry
    # different authority and different lifetimes.
    assert consume_token(db_session, raw, TokenPurpose.EMAIL_VERIFICATION) is None


def test_purge_removes_expired_tokens(db_session, user):
    raw = issue_token(db_session, user, TokenPurpose.PASSWORD_RESET)
    token = _rows(db_session)[0]
    token.expires_at = datetime.now(UTC) - timedelta(hours=1)
    db_session.commit()

    assert purge_expired(db_session) == 1
    assert _rows(db_session) == []
    assert consume_token(db_session, raw, TokenPurpose.PASSWORD_RESET) is None


def test_purge_removes_spent_tokens_that_have_not_expired(db_session, user):
    """The regression this function was carrying.

    A consumed token is dead the moment it is redeemed, but it kept its
    original `expires_at` — so filtering on age alone left it in the table
    for the rest of its TTL. Verification tokens live 24 hours, which is a
    day of spent rows per signup.
    """
    raw = issue_token(db_session, user, TokenPurpose.EMAIL_VERIFICATION)
    assert consume_token(db_session, raw, TokenPurpose.EMAIL_VERIFICATION) is not None

    spent = _rows(db_session)[0]
    assert spent.used_at is not None
    assert spent.expires_at > datetime.now(UTC).replace(tzinfo=None)

    assert purge_expired(db_session) == 1
    assert _rows(db_session) == []


def test_purge_keeps_tokens_that_are_still_usable(db_session, user):
    raw = issue_token(db_session, user, TokenPurpose.PASSWORD_RESET)

    assert purge_expired(db_session) == 0
    # Still redeemable afterwards: the prune must not cost anyone a live link.
    assert consume_token(db_session, raw, TokenPurpose.PASSWORD_RESET) is not None


def test_purge_honours_an_injected_clock(db_session, user):
    issue_token(db_session, user, TokenPurpose.PASSWORD_RESET)

    # An hour from now the reset token (1h TTL) has lapsed.
    later = datetime.now(UTC) + timedelta(hours=2)
    assert purge_expired(db_session, now=later) == 1
