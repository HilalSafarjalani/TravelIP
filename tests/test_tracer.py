"""Unit tests for backend.tracer line parsing against saved fixture output.

Fixtures in tests/fixtures/ are static text captures of real tracert/
traceroute output formats, used only to exercise the parser deterministically
-- they are never used as live application data.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.tracer import parse_line, build_command

FIXTURES = Path(__file__).parent / "fixtures"


def load_lines(name: str) -> list[str]:
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return text.splitlines()


# ---------------------------------------------------------------------------
# Windows tracert fixture
# ---------------------------------------------------------------------------


def test_windows_fixture_full_parse():
    lines = load_lines("tracert_windows.txt")
    hops = []
    done = False
    for line in lines:
        result = parse_line(line, os_kind="windows")
        if result is None:
            continue
        if result.get("done"):
            done = True
            continue
        hops.append(result)

    assert done is True
    assert len(hops) == 10

    # Hop 1: full 3-sample success
    h1 = hops[0]
    assert h1 == {
        "hop": 1,
        "ip": "192.168.1.1",
        "rtts": [2.0, 1.0, 1.0],
        "avg_rtt": pytest.approx(1.333, abs=0.01),
        "timeout": False,
    }

    # Hop 2: full timeout
    h2 = hops[1]
    assert h2["hop"] == 2
    assert h2["ip"] is None
    assert h2["rtts"] == [None, None, None]
    assert h2["avg_rtt"] is None
    assert h2["timeout"] is True

    # Hop 4: sub-millisecond "<1 ms" sample parses to a number
    h4 = hops[3]
    assert h4["ip"] == "100.64.0.1"
    assert h4["rtts"][2] == 1.0

    # Hop 7: partial timeout (one '*' sample among three)
    h7 = hops[6]
    assert h7["ip"] == "108.170.242.1"
    assert h7["rtts"] == [23.0, None, 24.0]
    assert h7["timeout"] is False
    assert h7["avg_rtt"] == pytest.approx(23.5)

    # Hop 9: IPv6 address
    h9 = hops[8]
    assert h9["ip"] == "2607:f8b0:4005:80a::200e"
    assert h9["timeout"] is False

    # Final hop reaches target
    h10 = hops[9]
    assert h10["ip"] == "8.8.8.8"


def test_windows_request_timed_out_variant():
    line = "  5     *        *        *     Request timed out."
    result = parse_line(line, os_kind="windows")
    assert result == {
        "hop": 5,
        "ip": None,
        "rtts": [None, None, None],
        "avg_rtt": None,
        "timeout": True,
    }


def test_windows_trace_complete_sentinel():
    assert parse_line("Trace complete.", os_kind="windows") == {"done": True}


def test_windows_banner_lines_ignored():
    assert parse_line("Tracing route to dns.google [8.8.8.8]", os_kind="windows") is None
    assert parse_line("over a maximum of 30 hops:", os_kind="windows") is None
    assert parse_line("", os_kind="windows") is None


# ---------------------------------------------------------------------------
# Linux/macOS traceroute fixture
# ---------------------------------------------------------------------------


def test_linux_fixture_full_parse():
    lines = load_lines("traceroute_linux.txt")
    hops = []
    for line in lines:
        result = parse_line(line, os_kind="unix")
        if result is not None:
            hops.append(result)

    assert len(hops) == 10

    h1 = hops[0]
    assert h1["hop"] == 1
    assert h1["ip"] == "192.168.1.1"
    assert h1["rtts"] == pytest.approx([0.456, 0.398, 0.371])
    assert h1["timeout"] is False

    h2 = hops[1]
    assert h2 == {
        "hop": 2,
        "ip": None,
        "rtts": [None, None, None],
        "avg_rtt": None,
        "timeout": True,
    }

    h7 = hops[6]
    assert h7["ip"] == "108.170.242.1"
    assert h7["rtts"] == pytest.approx([21.234, None, 21.876])
    assert h7["timeout"] is False

    h9 = hops[8]
    assert h9["ip"] == "2607:f8b0:4005:80a::200e"

    h10 = hops[9]
    assert h10["ip"] == "8.8.8.8"


def test_linux_star_star_star():
    result = parse_line(" 3  * * *", os_kind="unix")
    assert result == {
        "hop": 3,
        "ip": None,
        "rtts": [None, None, None],
        "avg_rtt": None,
        "timeout": True,
    }


def test_linux_banner_ignored():
    assert parse_line("traceroute to dns.google (8.8.8.8), 30 hops max, 60 byte packets", os_kind="unix") is None
    assert parse_line("", os_kind="unix") is None


def test_linux_annotation_flags_skipped():
    # Some traceroute builds append ICMP annotations like "!H" (host
    # unreachable) after an RTT sample; parser should not choke on them.
    result = parse_line(" 12  203.0.113.1  30.1 ms !H  29.9 ms !H  30.4 ms !H", os_kind="unix")
    assert result["ip"] == "203.0.113.1"
    assert result["rtts"] == pytest.approx([30.1, 29.9, 30.4])
    assert result["timeout"] is False


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------


def test_build_command_windows():
    cmd = build_command("8.8.8.8", os_kind="windows")
    assert cmd == ["tracert", "-d", "-w", "800", "-h", "30", "8.8.8.8"]


def test_build_command_unix():
    cmd = build_command("8.8.8.8", os_kind="unix")
    assert cmd == ["traceroute", "-n", "-w", "1", "-q", "3", "-m", "30", "8.8.8.8"]
