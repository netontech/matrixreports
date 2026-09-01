"""Authentication, and the refusal to serve staff data without it."""

from __future__ import annotations

import base64

import pytest

pytest.importorskip("flask")

from webapp.auth import (HASH_ENV, USER_ENV, InsecureBindError, check,  # noqa: E402
                         guard_bind, hash_password)


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv(USER_ENV, "hr")
    monkeypatch.setenv(HASH_ENV, hash_password("a-long-enough-secret"))


@pytest.fixture
def no_creds(monkeypatch):
    monkeypatch.delenv(USER_ENV, raising=False)
    monkeypatch.delenv(HASH_ENV, raising=False)


def header(user: str, password: str) -> dict:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


# --- the structural guarantee -------------------------------------------------

@pytest.mark.parametrize("host", ["0.0.0.0", "54.247.106.100", "::"])
def test_public_bind_without_a_login_is_refused(no_creds, host):
    """The whole point: staff data never reaches a public port unprotected."""
    with pytest.raises(InsecureBindError):
        guard_bind(host)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_loopback_without_a_login_is_allowed(no_creds, host):
    guard_bind(host)          # an SSH tunnel is the credential


def test_public_bind_with_a_login_is_allowed(creds):
    guard_bind("0.0.0.0")


# --- credential checking ------------------------------------------------------

def test_correct_credentials_pass(creds):
    assert check("hr", "a-long-enough-secret")


@pytest.mark.parametrize("user,password", [
    ("hr", "wrong"), ("wrong", "a-long-enough-secret"),
    ("", ""), ("hr", ""), ("HR", "a-long-enough-secret"),
])
def test_wrong_credentials_fail(creds, user, password):
    assert not check(user, password)


def test_nothing_passes_when_no_credentials_are_configured(no_creds):
    assert not check("hr", "a-long-enough-secret")


def test_the_hash_does_not_contain_the_password():
    assert "a-long-enough-secret" not in hash_password("a-long-enough-secret")


# --- the app --------------------------------------------------------------------

def test_requests_without_credentials_are_challenged(client, creds):
    response = client.get("/?report=daily&date=2026-06-01")
    assert response.status_code == 401
    assert "Basic" in response.headers["WWW-Authenticate"]


def test_requests_with_credentials_succeed(client, creds):
    response = client.get("/?report=daily&date=2026-06-01",
                          headers=header("hr", "a-long-enough-secret"))
    assert response.status_code == 200


def test_downloads_are_protected_too(client, creds):
    """A report is just as sensitive as a spreadsheet of it."""
    assert client.get("/download.xlsx?report=daily&date=2026-06-01").status_code == 401
    assert client.get("/download.csv?report=daily&date=2026-06-01").status_code == 401


def test_no_auth_configured_means_no_challenge(client, no_creds):
    """Loopback mode stays frictionless; guard_bind is what keeps it private."""
    assert client.get("/?report=daily&date=2026-06-01").status_code == 200
