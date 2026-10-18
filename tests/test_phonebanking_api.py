"""Phonebanking API tests — DB is mocked; no real PostgreSQL needed."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tests.conftest import mock_conn, mock_cursor

ORIGIN = {"Origin": "http://testserver"}
SESSION_COOKIE = "pb_session"

_SESS_COLS = ["session_token", "volunteer_email"]
_SESS_ROW = ("tok-abc", "caller@example.com")


@pytest.fixture(scope="module")
def client():
    with (
        patch("app.phonebanking.main.init_pools"),
        patch("app.phonebanking.main.purge_expired_rows"),
    ):
        from app.phonebanking.main import app

        with TestClient(app, raise_server_exceptions=True) as c:
            yield c


# Health / redirects


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_root_redirects_to_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (301, 302, 307, 308)
    assert "/pb/login" in r.headers["location"]


def test_login_page_ok(client):
    r = client.get("/pb/login")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


# Security headers


def test_security_headers_on_login_page(client):
    r = client.get("/pb/login")
    assert r.headers.get("X-Frame-Options") == "DENY"
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert "default-src" in r.headers.get("Content-Security-Policy", "")


# Login: input validation


def test_login_request_bad_email(client):
    r = client.post(
        "/pb/api/login/request", json={"email": "notanemail"}, headers=ORIGIN
    )
    assert r.status_code == 400


def test_login_request_missing_email(client):
    r = client.post("/pb/api/login/request", json={}, headers=ORIGIN)
    assert r.status_code == 400


def test_login_request_empty_email(client):
    r = client.post("/pb/api/login/request", json={"email": ""}, headers=ORIGIN)
    assert r.status_code == 400


def test_login_verify_missing_fields(client):
    r = client.post(
        "/pb/api/login/verify", json={"email": "a@example.test"}, headers=ORIGIN
    )
    assert r.status_code == 400


# CSRF (same-origin) guard


def test_login_request_blocked_without_origin(client):
    r = client.post("/pb/api/login/request", json={"email": "a@example.test"})
    assert r.status_code == 403


# Protected endpoints require session cookie


def test_call_page_unauthenticated(client):
    r = client.get("/pb/call")
    assert r.status_code == 401


def test_api_current_unauthenticated(client):
    r = client.get("/pb/api/current")
    assert r.status_code == 401


def test_api_next_unauthenticated(client):
    r = client.post("/pb/api/next", headers=ORIGIN)
    assert r.status_code == 401


def test_api_name_image_unauthenticated(client):
    r = client.get("/pb/api/name-image/00000000-0000-4000-8000-000000000001")
    assert r.status_code == 401


def test_api_call_unauthenticated(client):
    r = client.get("/pb/api/call/00000000-0000-4000-8000-000000000001")
    assert r.status_code == 401


# Authenticated: call page


def _session_cur():
    return mock_cursor(
        fetchone=_SESS_ROW,
        cols=_SESS_COLS,
    )


def test_call_page_authenticated(client):
    cur = _session_cur()
    conn = mock_conn(cur)
    with (
        patch("app.phonebanking.main.get_ops", return_value=conn),
        patch("app.phonebanking.main.put_ops"),
    ):
        client.cookies.set(SESSION_COOKIE, "tok-abc")
        r = client.get("/pb/call")
        client.cookies.clear()
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


# Submit: outcome validation


def test_submit_invalid_outcome(client):
    cur = _session_cur()
    conn = mock_conn(cur)
    with (
        patch("app.phonebanking.main.get_ops", return_value=conn),
        patch("app.phonebanking.main.put_ops"),
    ):
        client.cookies.set(SESSION_COOKIE, "tok-abc")
        r = client.post(
            "/pb/api/submit/00000000-0000-4000-8000-000000000001",
            json={"outcome": "invalid"},
            headers=ORIGIN,
        )
        client.cookies.clear()
    assert r.status_code == 400


def test_submit_rejects_expired_assignment(client):
    session_cur = _session_cur()
    assignment_cur = mock_cursor(cols=["contact_id", "active", "fresh"])
    assignment_cur.fetchone.side_effect = [("contact-1", True, False), None]
    with (
        patch(
            "app.phonebanking.main.get_ops",
            side_effect=[mock_conn(session_cur), mock_conn(assignment_cur)],
        ),
        patch("app.phonebanking.main.put_ops"),
    ):
        client.cookies.set(SESSION_COOKIE, "tok-abc")
        response = client.post(
            "/pb/api/submit/00000000-0000-4000-8000-000000000001",
            json={"outcome": "answered"},
            headers=ORIGIN,
        )
        client.cookies.clear()
    assert response.status_code == 409
    assert "expired" in response.json()["detail"]
