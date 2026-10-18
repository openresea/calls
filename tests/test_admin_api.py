"""Admin API tests — DB is mocked; no real PostgreSQL needed."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.common.crypto import encrypt_text
from tests.conftest import mock_conn, mock_cursor

ORIGIN = {"Origin": "http://testserver"}
ADMIN_COOKIE = "pb_admin_session"

_ADMIN_COLS = ["session_token", "admin_email"]
_ADMIN_ROW = ("admin-tok", "admin@example.com")


@pytest.fixture(scope="module")
def client():
    with patch("app.admin.main.init_pools"), patch("app.admin.main.purge_expired_rows"):
        from app.admin.main import app

        with TestClient(app, raise_server_exceptions=True) as c:
            yield c


# Health / redirects


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["service"] == "admin"


def test_root_redirects_to_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (301, 302, 307, 308)
    assert "/login" in r.headers["location"]


def test_login_page_ok(client):
    r = client.get("/login")
    assert r.status_code == 200


# Security headers


def test_security_headers_on_login_page(client):
    r = client.get("/login")
    assert r.headers.get("X-Frame-Options") == "DENY"
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert "default-src" in r.headers.get("Content-Security-Policy", "")


# Login: input validation


def test_login_request_bad_email(client):
    r = client.post("/api/login/request", json={"email": "bad"}, headers=ORIGIN)
    assert r.status_code == 400


def test_login_request_partial_email(client):
    r = client.post("/api/login/request", json={"email": "@"}, headers=ORIGIN)
    assert r.status_code == 400


def test_login_verify_missing_code(client):
    r = client.post(
        "/api/login/verify", json={"email": "a@example.test"}, headers=ORIGIN
    )
    assert r.status_code == 400


# CSRF guard


def test_login_request_blocked_without_origin(client):
    r = client.post("/api/login/request", json={"email": "a@example.test"})
    assert r.status_code == 403


# Protected endpoints require admin session


def test_dashboard_unauthenticated(client):
    r = client.get("/dashboard", follow_redirects=False)
    assert r.status_code in (302, 307, 401)


def test_api_stats_unauthenticated(client):
    r = client.get("/api/stats")
    assert r.status_code == 401


def test_api_contacts_unauthenticated(client):
    r = client.get("/api/contacts")
    assert r.status_code == 401


def test_api_results_unauthenticated(client):
    r = client.get("/api/results")
    assert r.status_code == 401


def test_api_upload_unauthenticated(client):
    r = client.post("/api/upload", headers=ORIGIN)
    assert r.status_code == 401


def test_api_volunteers_unauthenticated(client):
    r = client.get("/api/volunteers")
    assert r.status_code == 401


# Authenticated: dashboard


def _admin_cur():
    return mock_cursor(fetchone=_ADMIN_ROW, cols=_ADMIN_COLS)


def test_dashboard_authenticated(client):
    cur = _admin_cur()
    conn = mock_conn(cur)
    with (
        patch("app.admin.main.get_ops", return_value=conn),
        patch("app.admin.main.put_ops"),
    ):
        client.cookies.set(ADMIN_COOKIE, "admin-tok")
        r = client.get("/dashboard")
        client.cookies.clear()
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


# Volunteer management


def test_add_volunteer_bad_email(client):
    auth_cur = _admin_cur()
    conn = mock_conn(auth_cur)
    with (
        patch("app.admin.main.get_ops", return_value=conn),
        patch("app.admin.main.put_ops"),
    ):
        client.cookies.set(ADMIN_COOKIE, "admin-tok")
        r = client.post("/api/volunteers", json={"email": "notvalid"}, headers=ORIGIN)
        client.cookies.clear()
    assert r.status_code == 400


def test_export_results_works_with_named_cursor_without_description(client):
    auth_cur = _admin_cur()
    export_cur = mock_cursor(cols=None)
    export_cur.description = None
    export_cur.__iter__.return_value = iter(
        [
            (
                "caller@example.com",
                "answered",
                "Export smoke",
                "2026-04-29T01:02:03+00:00",
                encrypt_text("CSV Person"),
                encrypt_text("+15555550123"),
                None,  # saved before notes were encrypted
            )
        ]
    )

    with (
        patch(
            "app.admin.main.get_ops",
            side_effect=[mock_conn(auth_cur), mock_conn(export_cur)],
        ),
        patch("app.admin.main.put_ops"),
    ):
        client.cookies.set(ADMIN_COOKIE, "admin-tok")
        r = client.get("/api/results/export.csv")
        client.cookies.clear()

    assert r.status_code == 200
    assert r.headers["content-disposition"] == "attachment; filename=pb_results.csv"
    assert "text/csv" in r.headers["content-type"]
    assert "name,phone,caller_email,outcome,comments,submitted_at" in r.text
    assert "CSV Person,'+15555550123,caller@example.com,answered,Export smoke" in r.text
