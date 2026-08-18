"""Reusable annotated field types shared by the request schemas."""

from typing import Annotated

from pydantic import AfterValidator, EmailStr


def _normalize_email(value: str) -> str:
    """Fold an address to its canonical form: trimmed and lowercased.

    RFC 5321 says the local part is technically case-sensitive, but no
    mailbox provider in practice treats "Ada@example.com" and
    "ada@example.com" as different people. Storing both would let one human
    hold two accounts on one inbox, and would make "email already
    registered" depend on which shift key they pressed at signup.
    """
    return value.strip().lower()


# Every schema that accepts an address a user typed should use this rather
# than EmailStr, so lookups and uniqueness see one canonical spelling.
NormalizedEmail = Annotated[EmailStr, AfterValidator(_normalize_email)]
