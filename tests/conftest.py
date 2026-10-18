import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet

# Fernet key fixture


@pytest.fixture(scope="session")
def fernet_key():
    return Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def set_fernet_env(fernet_key, monkeypatch):
    monkeypatch.setenv("FERNET_KEY", fernet_key)
    monkeypatch.setenv("EMAIL_CODE_SECRET", "test-only-secret-" * 4)
    import app.common.crypto as m

    m._fernet_instance = None
    yield
    m._fernet_instance = None


# Mock DB helpers


def mock_cursor(fetchone=None, fetchall=None, cols=None):
    """Return a mock psycopg2 cursor."""
    cur = MagicMock()
    cur.__enter__ = lambda self: self
    cur.__exit__ = MagicMock(return_value=False)
    cur.description = [SimpleNamespace(name=c) for c in (cols or [])]
    cur.fetchone.return_value = fetchone
    cur.fetchall.return_value = fetchall or []
    return cur


def mock_conn(*cursors):
    """Return a mock psycopg2 connection that yields cursors in order."""
    conn = MagicMock()
    conn.__enter__ = lambda self: self
    conn.__exit__ = MagicMock(return_value=False)
    if len(cursors) == 1:
        conn.cursor.return_value = cursors[0]
    else:
        conn.cursor.side_effect = list(cursors)
    return conn


@pytest.fixture
def live_db(monkeypatch):
    """Actual least-privilege services, on fresh disposable databases only."""
    import secrets
    import uuid
    from pathlib import Path

    import psycopg2
    from fastapi.testclient import TestClient
    from psycopg2 import sql
    from psycopg2.pool import ThreadedConnectionPool

    from app.admin.main import app as admin_app
    from app.common import db
    from app.common.crypto import encrypt_text
    from app.phonebanking.main import app as caller_app

    dsn = os.environ.get("TEST_DATABASE_DSN")
    if not dsn:
        pytest.skip("Set TEST_DATABASE_DSN to an isolated PostgreSQL test cluster.")
    settings = psycopg2.extensions.parse_dsn(dsn)
    root = psycopg2.connect(dsn)
    root.autocommit = True
    name = "test_calls_" + uuid.uuid4().hex[:12]
    pools = []
    conn = None
    monkeypatch.setenv("EMAIL_CODE_SECRET", secrets.token_urlsafe(48))
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_FROM", "login@example.test")
    try:
        with root.cursor() as cur:
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        settings["dbname"] = name
        conn = psycopg2.connect(**settings)
        with conn, conn.cursor() as cur:
            cur.execute(Path("app/sql/schema.sql").read_text())
            cur.execute(
                "INSERT INTO pb_admin_users(email) VALUES ('admin@example.test')"
            )
            cur.execute(
                "INSERT INTO pb_authorized_volunteers(email) VALUES ('caller@example.test'),('other@example.test')"
            )
            for i in range(3):
                cur.execute(
                    "INSERT INTO pb_contacts(name_cipher,phone_cipher) VALUES (%s,%s)",
                    (
                        encrypt_text(f"Sample Contact {i}"),
                        encrypt_text("+447700900001"),
                    ),
                )
            cur.execute(
                "INSERT INTO pb_admin_sessions(admin_email) VALUES ('admin@example.test') RETURNING session_token"
            )
            admin_token = cur.fetchone()[0]
            caller_tokens = []
            for email in [
                "caller@example.test",
                "caller@example.test",
                "other@example.test",
            ]:
                cur.execute(
                    "INSERT INTO pb_sessions(volunteer_email) VALUES (%s) RETURNING session_token",
                    (email,),
                )
                caller_tokens.append(cur.fetchone()[0])
        password = secrets.token_urlsafe(32)
        with root.cursor() as cur:
            for role in ["calls_admin", "calls_caller"]:
                cur.execute(
                    sql.SQL("ALTER ROLE {} PASSWORD %s").format(sql.Identifier(role)),
                    (password,),
                )
        for role in ["calls_admin", "calls_caller"]:
            pools.append(
                ThreadedConnectionPool(
                    1, 10, **{**settings, "user": role, "password": password}
                )
            )

        class Client(TestClient):
            def __init__(self, app, pool, cookie, token):
                super().__init__(
                    app,
                    base_url="https://testserver",
                    headers={"Origin": "https://testserver"},
                )
                self.pool = pool
                self.cookies.set(cookie, token)

            def request(self, *args, **kwargs):
                db._ops_pool = self.pool
                return super().request(*args, **kwargs)

        yield SimpleNamespace(
            owner=conn,
            admin=Client(admin_app, pools[0], "pb_admin_session", admin_token),
            caller=Client(caller_app, pools[1], "pb_session", caller_tokens[0]),
            second=Client(caller_app, pools[1], "pb_session", caller_tokens[1]),
            other=Client(caller_app, pools[1], "pb_session", caller_tokens[2]),
            pools=pools,
        )
    finally:
        db._ops_pool = None
        for pool in pools:
            pool.closeall()
        if conn:
            conn.close()
        with root.cursor() as cur:
            cur.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(name)
                )
            )
        root.close()
