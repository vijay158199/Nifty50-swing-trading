"""Single-user login gate. Not multi-tenant auth - this app is meant for one
person, but once deployed publicly this is what keeps trade data and the
delete/backtest controls from being open to anyone with the URL.
"""
from __future__ import annotations

import secrets

from fastapi import Request

from app.config import settings

SESSION_KEY = "authenticated"


class NotAuthenticatedError(Exception):
    """Raised by `require_login` and translated to a redirect by main.py's
    exception handler."""


def verify_credentials(username: str, password: str) -> bool:
    user_ok = secrets.compare_digest(username.strip(), settings.auth_username)
    pass_ok = secrets.compare_digest(password, settings.auth_password)
    return user_ok and pass_ok


def require_login(request: Request) -> None:
    if not request.session.get(SESSION_KEY):
        raise NotAuthenticatedError()


def is_using_default_password() -> bool:
    return settings.auth_password == "changeme123"
