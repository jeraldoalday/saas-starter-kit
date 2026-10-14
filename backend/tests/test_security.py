"""`app.core.security`'s password hashing, direct and unmediated by the API.

Nothing here exercises an endpoint: `test_auth.py` and `test_password_reset.py`
already cover those paths through HTTP. What is missing, and what this file
is for, is `verify_password`'s own contract — in particular, that a corrupt
or malformed hash in the database fails a login instead of raising, since a
500 on that path is a crash a bad row in the users table could trigger at any
time, not just in a test that goes looking for it.
"""

import pytest

from app.core.security import (
    MAX_PASSWORD_BYTES,
    PasswordTooLongError,
    hash_password,
    verify_password,
)


def test_hash_and_verify_round_trip():
    hashed = hash_password("correct-horse-battery")
    assert verify_password("correct-horse-battery", hashed)


def test_verify_rejects_the_wrong_password():
    hashed = hash_password("correct-horse-battery")
    assert not verify_password("wrong-password", hashed)


def test_hash_rejects_a_password_over_the_byte_limit():
    # One byte over, in bytes rather than characters, per MAX_PASSWORD_BYTES'
    # own note that the two are not the same thing for non-ASCII input.
    too_long = "a" * (MAX_PASSWORD_BYTES + 1)
    with pytest.raises(PasswordTooLongError):
        hash_password(too_long)


def test_hash_rejects_based_on_utf8_bytes_not_characters():
    # 72 "é" characters are 144 bytes once UTF-8 encoded, well over the
    # limit, even though the string is well under 72 *characters*.
    with pytest.raises(PasswordTooLongError):
        hash_password("é" * 72)


def test_verify_rejects_an_overlong_candidate_without_hashing_it():
    hashed = hash_password("correct-horse-battery")
    too_long = "a" * (MAX_PASSWORD_BYTES + 1)
    assert not verify_password(too_long, hashed)


def test_verify_treats_a_malformed_stored_hash_as_a_failed_login():
    # A row that is not a valid bcrypt hash at all — truncated by a bad
    # migration, hand-edited, whatever got it there — must fail the login
    # the way a wrong password does, not raise and turn one bad row into a
    # 500 for that account.
    assert not verify_password("correct-horse-battery", "not-a-bcrypt-hash")


def test_verify_treats_an_empty_stored_hash_as_a_failed_login():
    assert not verify_password("correct-horse-battery", "")
