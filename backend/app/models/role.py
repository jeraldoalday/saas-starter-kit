from enum import StrEnum


class Role(StrEnum):
    """Account roles, ordered from least to most privileged.

    A flat hierarchy rather than per-permission grants: three levels cover
    what a single-tenant SaaS needs on day one, and the ordering below lets
    a route state a minimum instead of enumerating roles.
    """

    MEMBER = "member"
    ADMIN = "admin"
    OWNER = "owner"


# Higher rank satisfies any lower requirement.
_RANK: dict[str, int] = {
    Role.MEMBER.value: 0,
    Role.ADMIN.value: 1,
    Role.OWNER.value: 2,
}


def rank(role: str) -> int:
    """Numeric rank of a role; unknown values sort lowest, denying access."""
    return _RANK.get(role, -1)


def satisfies(actual: str, required: Role) -> bool:
    return rank(actual) >= rank(required.value)
