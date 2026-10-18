"""Tests for pure helper functions that don't touch the database."""

from datetime import UTC

import pytest
from fastapi import HTTPException

# _safe_csv
from app.admin.main import _EMAIL_RE, _parse_volunteer_expiry, _safe_csv
from app.admin.main import _cookie_secure as admin_cookie_secure
from app.phonebanking.main import _cookie_secure as pb_cookie_secure


@pytest.mark.parametrize(
    "inp,expected",
    [
        ("normal text", "normal text"),
        ("", ""),
        ("=HYPERLINK()", "'=HYPERLINK()"),
        ("+SUM(A1)", "'+SUM(A1)"),
        ("-1+2", "'-1+2"),
        ("@user", "'@user"),
        ("\t tabbed", "'\t tabbed"),
        (None, ""),
        (42, "42"),
    ],
)
def test_safe_csv(inp, expected):
    assert _safe_csv(inp) == expected


def test_parse_volunteer_expiry_empty():
    assert _parse_volunteer_expiry(None) is None
    assert _parse_volunteer_expiry("") is None


def test_parse_volunteer_expiry_date_uses_end_of_day():
    parsed = _parse_volunteer_expiry("2026-04-29")
    assert parsed.year == 2026
    assert parsed.month == 4
    assert parsed.day == 29
    assert parsed.hour == 23
    assert parsed.tzinfo is UTC


def test_parse_volunteer_expiry_iso_normalizes_to_utc():
    parsed = _parse_volunteer_expiry("2026-04-29T20:30:00+01:00")
    assert parsed.isoformat() == "2026-04-29T19:30:00+00:00"


@pytest.mark.parametrize("inp", ["bad-date", "2026-99-99", 123])
def test_parse_volunteer_expiry_rejects_invalid(inp):
    with pytest.raises(HTTPException) as exc:
        _parse_volunteer_expiry(inp)
    assert exc.value.status_code == 400


# _cookie_secure


@pytest.mark.parametrize(
    "val,expected",
    [
        ("true", True),
        ("1", True),
        ("yes", True),
        ("True", True),
        ("false", False),
        ("0", False),
        ("no", False),
        ("False", False),
    ],
)
def test_cookie_secure_admin(monkeypatch, val, expected):
    monkeypatch.setenv("COOKIE_SECURE", val)
    assert (
        admin_cookie_secure() is True
    )  # secure sessions cannot be disabled by configuration


def test_cookie_secure_default_is_true(monkeypatch):
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    assert admin_cookie_secure() is True
    assert pb_cookie_secure() is True


# email regex


@pytest.mark.parametrize(
    "email",
    [
        "user@example.com",
        "a+b@sub.example.org",
        "123@example.test",
    ],
)
def test_email_valid(email):
    assert _EMAIL_RE.match(email)


@pytest.mark.parametrize(
    "email",
    [
        "",
        "@",
        "foo@",
        "@bar.com",
        "no-at-sign",
        "space @example.com",
    ],
)
def test_email_invalid(email):
    assert not _EMAIL_RE.match(email)
