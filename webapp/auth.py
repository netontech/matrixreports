"""HTTP Basic authentication.

The reports carry staff names, arrival times and hours. That is fine behind
loopback, where reaching the port already means holding an SSH key; it is not
fine on a public address. So the rule enforced here is simple and structural:

    binding anywhere other than loopback requires credentials to be configured.

The app refuses to start otherwise, rather than coming up unprotected and
leaving someone to notice later. The password is only ever held as a hash -
nothing in the environment or the process list carries the plaintext.
"""

from __future__ import annotations

import hmac
import os

from flask import Response, request
from werkzeug.security import check_password_hash, generate_password_hash

USER_ENV = "MATRIXREPORTS_AUTH_USER"
HASH_ENV = "MATRIXREPORTS_AUTH_PASSWORD_HASH"

LOOPBACK = {"127.0.0.1", "::1", "localhost"}


class InsecureBindError(RuntimeError):
    """Raised when a public bind is attempted with no credentials set."""


def credentials() -> tuple[str, str] | None:
    user = os.environ.get(USER_ENV, "").strip()
    password_hash = os.environ.get(HASH_ENV, "").strip()
    if user and password_hash:
        return user, password_hash
    return None


def guard_bind(host: str) -> None:
    """Refuse to serve staff data on a public address without a login."""
    if host in LOOPBACK or credentials():
        return
    raise InsecureBindError(
        f"refusing to bind {host} without authentication.\n\n"
        "These reports contain employee names and hours. Set a login first:\n\n"
        f"  export {USER_ENV}=hr\n"
        f"  export {HASH_ENV}=\"$(matrixreports-web --hash-password)\"\n\n"
        "or bind 127.0.0.1 and reach it over an SSH tunnel."
    )


def check(supplied_user: str, supplied_password: str) -> bool:
    creds = credentials()
    if creds is None:
        return False
    user, password_hash = creds
    # Compare both, always, so a wrong username costs the same as a wrong password.
    user_ok = hmac.compare_digest(supplied_user or "", user)
    password_ok = check_password_hash(password_hash, supplied_password or "")
    return user_ok and password_ok


def unauthorised() -> Response:
    return Response(
        "Authentication required.", 401,
        {"WWW-Authenticate": 'Basic realm="Attendance reports", charset="UTF-8"'},
    )


def require_login():
    """Flask before_request hook. Returns a response to short-circuit, or None."""
    if credentials() is None:
        return None                     # loopback-only mode; guard_bind enforced it
    auth = request.authorization
    if auth is None or not check(auth.username or "", auth.password or ""):
        return unauthorised()
    return None


def hash_password(plaintext: str) -> str:
    return generate_password_hash(plaintext)
