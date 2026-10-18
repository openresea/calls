"""Tests for pure helper functions that don't touch the database."""

from datetime import UTC

import pytest
from fastapi import HTTPException

from app.admin.main import _parse_volunteer_expiry, _safe_csv
from app.common.email_auth import normalize_email


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


# Email addresses (used for sign-in and when adding callers)


@pytest.mark.parametrize(
    "email",
    [
        "user@example.com",
        "a+b@sub.example.org",
        "123@example.test",
        " Mixed@Example.COM ",
    ],
)
def test_email_valid(email):
    assert normalize_email(email) == email.strip().lower()


@pytest.mark.parametrize(
    "email",
    [
        "",
        "@",
        "foo@",
        "@bar.com",
        "no-at-sign",
        "space @example.com",
        "a..b@example.com",
        None,
    ],
)
def test_email_invalid(email):
    with pytest.raises(HTTPException) as exc:
        normalize_email(email)
    assert exc.value.status_code == 400
