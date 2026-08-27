from pydantic import BaseModel, Field

DEFAULT_TEST_ALERT_MESSAGE = (
    "This is a test alert from the SaaS Starter API. If you can read it, "
    "alert routing works."
)


class AlertTestRequest(BaseModel):
    """Body of the alert-routing self-test."""

    message: str = Field(
        default=DEFAULT_TEST_ALERT_MESSAGE, min_length=1, max_length=280
    )


class AlertTestResponse(BaseModel):
    delivered: bool
    # Which transport handled it, so the answer to "nothing arrived" starts
    # with knowing whether the webhook was configured at all.
    backend: str


class AdminOverview(BaseModel):
    """Counts an operator wants before they want a dashboard.

    Deliberately counts rather than lists: the question this answers is "is
    anything obviously wrong right now", and every figure here is one an
    operator can sanity-check against what they expect. Anything needing a
    breakdown already has its own endpoint — /admin/users for accounts,
    /analytics/summary for behaviour over a window.
    """

    total_users: int
    active_users: int
    verified_users: int

    # Credentials that could reach the API this second. The active/total
    # split is the useful part: a large gap means a lot of revoked or
    # expired rows waiting on the retention prune.
    total_api_keys: int
    total_sessions: int
    active_sessions: int

    # Keyed by SubscriptionStatus. Absent statuses mean zero rather than
    # being reported as zero, so a status added later needs no change here.
    subscriptions_by_status: dict[str, int]

    # The security half of "is anything obviously wrong right now". Refused
    # sign-ins over a short window rather than a total, because the total is
    # a number that only ever goes up and so answers nothing: what an
    # operator wants to know is whether somebody is trying accounts *now*.
    failed_logins_last_hour: int
    # The trail's own size, for the question the retention prune answers. A
    # figure far above what the window should hold means the scheduled prune
    # has not been running.
    audit_entries: int
