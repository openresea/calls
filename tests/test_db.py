from unittest.mock import MagicMock

from app.common import db


class DummyCursor:
    def __init__(self):
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql):
        self.statements.append(sql)


class DummyConn:
    def __init__(self, *, closed=False):
        self.closed = closed
        self.cursor_obj = DummyCursor()

    def cursor(self):
        return self.cursor_obj


def test_get_ops_replaces_closed_connection():
    closed_conn = DummyConn(closed=True)
    good_conn = DummyConn()
    pool = MagicMock()
    pool.getconn.side_effect = [closed_conn, good_conn]

    db._ops_pool = pool
    try:
        assert db.get_ops() is good_conn
    finally:
        db._ops_pool = None

    pool.putconn.assert_called_once_with(closed_conn, close=True)
    assert good_conn.cursor_obj.statements == ["SELECT 1"]


def test_put_ops_discards_closed_connection():
    conn = DummyConn(closed=True)
    pool = MagicMock()

    db._ops_pool = pool
    try:
        db.put_ops(conn)
    finally:
        db._ops_pool = None

    pool.putconn.assert_called_once_with(conn, close=True)
