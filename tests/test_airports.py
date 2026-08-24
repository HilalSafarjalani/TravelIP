"""Tests for backend.airports: reverse-DNS hostname -> city inference."""

from __future__ import annotations

import pytest

from backend.airports import find_airport_code_in_hostname


@pytest.mark.parametrize(
    "hostname,expected_code,expected_city",
    [
        ("ae0.cr1.lax1.us.example.net", "LAX", "Los Angeles"),
        ("xe-0-0-0.lax20.ip4.example.net", "LAX", "Los Angeles"),
        ("core-fra-1.isp.net", "FRA", "Frankfurt"),
        ("router.sfo.example.com", "SFO", "San Francisco"),
        ("ROUTER.LAX1.EXAMPLE.NET", "LAX", "Los Angeles"),
    ],
)
def test_matches_known_airport_code_label(hostname, expected_code, expected_city):
    hit = find_airport_code_in_hostname(hostname)
    assert hit is not None
    assert hit["code"] == expected_code
    assert hit["city"] == expected_city


def test_returns_none_for_no_match():
    assert find_airport_code_in_hostname("core-router-1.internal.example.net") is None


def test_returns_none_for_none_or_empty_hostname():
    assert find_airport_code_in_hostname(None) is None
    assert find_airport_code_in_hostname("") is None


def test_does_not_match_substring_within_a_longer_label():
    # "laxative.example.net" contains "lax" as a substring but the whole
    # label isn't a bare code -- must not match.
    assert find_airport_code_in_hostname("laxative.example.net") is None


def test_does_not_match_common_networking_tokens():
    # "gig0" would match the 3-letters-plus-digits shape, but GIG (Rio de
    # Janeiro) is deliberately excluded from the table for exactly this
    # collision -- see backend/airports.py.
    assert find_airport_code_in_hostname("te-0-0-0.gig0.example.net") is None


def test_matches_code_with_trailing_digits():
    hit = find_airport_code_in_hostname("ae3.lax99.example.net")
    assert hit is not None
    assert hit["code"] == "LAX"
