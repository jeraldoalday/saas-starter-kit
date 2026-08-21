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
