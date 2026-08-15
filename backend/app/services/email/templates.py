"""Transactional email bodies.

Plain f-strings rather than a template engine: there are few enough
messages that a Jinja dependency and a templates directory would cost more
than they save. Swap in a renderer here if the set grows.
"""

from app.services.email.base import EmailMessage

APP_NAME = "SaaS Starter"


def verification_email(to: str, verify_url: str) -> EmailMessage:
    return EmailMessage(
        to=to,
        subject=f"Confirm your {APP_NAME} email address",
        text_body=(
            f"Welcome to {APP_NAME}.\n\n"
            f"Confirm your email address by opening this link:\n{verify_url}\n\n"
            "The link expires in 24 hours. If you didn't create an account, "
            "you can ignore this message."
        ),
        html_body=(
            f"<p>Welcome to {APP_NAME}.</p>"
            f'<p><a href="{verify_url}">Confirm your email address</a></p>'
            "<p>The link expires in 24 hours. If you didn't create an account, "
            "you can ignore this message.</p>"
        ),
    )


def password_reset_email(to: str, reset_url: str) -> EmailMessage:
    return EmailMessage(
        to=to,
        subject=f"Reset your {APP_NAME} password",
        text_body=(
            "We received a request to reset your password.\n\n"
            f"Choose a new one here:\n{reset_url}\n\n"
            "The link expires in 1 hour and can be used once. If you didn't "
            "request this, no action is needed - your password is unchanged."
        ),
        html_body=(
            "<p>We received a request to reset your password.</p>"
            f'<p><a href="{reset_url}">Choose a new password</a></p>'
            "<p>The link expires in 1 hour and can be used once. If you didn't "
            "request this, no action is needed &mdash; your password is unchanged.</p>"
        ),
    )
