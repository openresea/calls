from types import SimpleNamespace
from unittest.mock import MagicMock

from app.common.util import fetchall_dict, fetchone_dict


def _cur(cols, rows):
    cur = MagicMock()
    cur.description = [SimpleNamespace(name=c) for c in cols]
    cur.fetchone.return_value = rows[0] if rows else None
    cur.fetchall.return_value = rows
    return cur


def test_fetchone_dict_returns_dict():
    cur = _cur(["id", "name"], [(1, "Alice")])
    assert fetchone_dict(cur) == {"id": 1, "name": "Alice"}


def test_fetchone_dict_returns_none_on_empty():
    cur = _cur(["id"], [])
    assert fetchone_dict(cur) is None


def test_fetchall_dict_returns_list_of_dicts():
    cur = _cur(["a", "b"], [(1, 2), (3, 4)])
    assert fetchall_dict(cur) == [{"a": 1, "b": 2}, {"a": 3, "b": 4}]


def test_fetchall_dict_empty():
    cur = _cur(["x"], [])
    assert fetchall_dict(cur) == []
