from __future__ import annotations

import pytest

from backend.validation import InvalidTargetError, validate_target


@pytest.mark.parametrize(
    "target",
    [
        "8.8.8.8",
        "2001:4860:4860::8888",
        "dns.google",
        "example.com",
        "a.b-c.example.co",
        "localhost",
    ],
)
def test_valid_targets_accepted(target):
    assert validate_target(target)


@pytest.mark.parametrize(
    "target",
    [
        "google.com; rm -rf /",
        "google.com && whoami",
        "google.com | cat /etc/passwd",
        "$(whoami)",
        "`whoami`",
        "-h",
        "--help",
        "8.8.8.8 8.8.4.4",
        "",
        "   ",
        "a" * 300,
        "goo gle.com",
        "-8.8.8.8",
    ],
)
def test_invalid_targets_rejected(target):
    with pytest.raises(InvalidTargetError):
        validate_target(target)
