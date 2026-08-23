"""Real traceroute engine.

Spawns the OS-native trace tool (tracert on Windows, traceroute on
Linux/macOS) as an asyncio subprocess and streams parsed hops as they
arrive on stdout -- never buffers the whole trace before yielding.

No traceroute data is ever fabricated. A hop that times out is emitted
with timeout=True and ip=None; we never invent an IP or RTT.
"""

from __future__ import annotations

import asyncio
import platform
import re
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

from backend.validation import is_ip_address

# ---------------------------------------------------------------------------
# OS detection / command construction
# ---------------------------------------------------------------------------


def detect_os_kind() -> str:
    """Return 'windows' or 'unix' for the current platform."""
    return "windows" if platform.system() == "Windows" else "unix"


def build_command(target: str, os_kind: Optional[str] = None) -> list[str]:
    """Build the argv list for the trace subprocess. No shell involved."""
    os_kind = os_kind or detect_os_kind()
    if os_kind == "windows":
        return ["tracert", "-d", "-w", "800", "-h", "30", target]
    return ["traceroute", "-n", "-w", "1", "-q", "3", "-m", "30", target]


# ---------------------------------------------------------------------------
# Line parsing
# ---------------------------------------------------------------------------

# Windows tracert line, e.g.:
#   "  1     2 ms     1 ms     1 ms  192.168.1.1"
#   "  2     *        *        *     Request timed out."
#   "  3    15 ms     *       14 ms  10.20.30.1"
_WIN_HOP_RE = re.compile(r"^\s*(\d+)\s+(.*\S)\s*$")
_WIN_SAMPLE_RE = re.compile(
    r"^(\*|<?\d+\s*ms)\s+(\*|<?\d+\s*ms)\s+(\*|<?\d+\s*ms)\s+(.*)$"
)
_WIN_NUM_RE = re.compile(r"(\d+)")

# Linux/macOS traceroute line, e.g.:
#   " 1  192.168.1.1  0.456 ms  0.398 ms  0.371 ms"
#   " 3  * * *"
#   " 4  72.14.215.85  20.123 ms  19.876 ms *"
_UNIX_HOP_RE = re.compile(r"^\s*(\d+)\s+(.*\S)\s*$")


def _mk_hop(hop: int, ip: Optional[str], rtts: list, timeout: bool) -> dict:
    valid = [r for r in rtts if r is not None]
    avg = round(sum(valid) / len(valid), 3) if valid else None
    return {
        "hop": hop,
        "ip": ip,
        "rtts": rtts,
        "avg_rtt": avg,
        "timeout": timeout,
    }


def _parse_windows_line(line: str) -> Optional[dict]:
    line = line.rstrip("\r\n")
    stripped = line.strip()
    if not stripped:
        return None
    if stripped.lower().startswith("tracing route"):
        return None
    if stripped.lower().startswith("trace complete"):
        return {"done": True}
    if stripped.lower().startswith("over a maximum"):
        return None

    m = _WIN_HOP_RE.match(line)
    if not m:
        return None
    try:
        hop_num = int(m.group(1))
    except ValueError:
        return None
    rest = m.group(2).strip()

    m2 = _WIN_SAMPLE_RE.match(rest)
    if not m2:
        return None

    tokens = [m2.group(i) for i in (1, 2, 3)]
    dest = m2.group(4).strip()

    rtts: list[Optional[float]] = []
    for tok in tokens:
        if tok == "*":
            rtts.append(None)
        else:
            num_m = _WIN_NUM_RE.search(tok)
            rtts.append(float(num_m.group(1)) if num_m else None)

    dest_lower = dest.lower()
    if dest_lower.startswith("request timed out"):
        return _mk_hop(hop_num, None, rtts, timeout=True)

    if "destination host unreachable" in dest_lower or "destination net unreachable" in dest_lower:
        return _mk_hop(hop_num, None, rtts, timeout=True)

    # -d suppresses reverse DNS, so dest should be a bare IP. Be defensive
    # and take the last whitespace-delimited token in case of stray output.
    candidate = dest.split()[-1] if dest else ""
    ip = candidate if is_ip_address(candidate) else None
    return _mk_hop(hop_num, ip, rtts, timeout=ip is None)


def _parse_unix_line(line: str) -> Optional[dict]:
    line = line.rstrip("\r\n")
    stripped = line.strip()
    if not stripped:
        return None
    if stripped.lower().startswith("traceroute to"):
        return None

    m = _UNIX_HOP_RE.match(line)
    if not m:
        return None
    try:
        hop_num = int(m.group(1))
    except ValueError:
        return None
    rest = m.group(2).strip()

    # Full timeout: "* * *" (any run length, -q controls how many stars)
    if re.fullmatch(r"(\*\s*)+", rest):
        stars = rest.split()
        return _mk_hop(hop_num, None, [None] * len(stars), timeout=True)

    tokens = rest.split()
    ip: Optional[str] = None
    rtts: list[Optional[float]] = []
    i = 0
    if tokens and is_ip_address(tokens[0].strip("()")):
        ip = tokens[0].strip("()")
        i = 1

    while i < len(tokens):
        tok = tokens[i]
        if tok == "*":
            rtts.append(None)
            i += 1
            continue
        # A probe may report from a different intermediate IP than the hop's
        # primary IP (rare, asymmetric routing); we keep the first IP seen
        # and skip any subsequent bracketed/plain IP tokens.
        if is_ip_address(tok.strip("()")):
            i += 1
            continue
        try:
            val = float(tok)
        except ValueError:
            i += 1
            continue
        rtts.append(val)
        i += 1
        if i < len(tokens) and tokens[i] == "ms":
            i += 1
        # skip annotation flags like "!H", "!N"
        while i < len(tokens) and tokens[i].startswith("!"):
            i += 1

    if not rtts:
        # Nothing parseable at all -- treat conservatively as a timeout
        # rather than silently dropping the hop.
        return _mk_hop(hop_num, ip, [None, None, None], timeout=ip is None)

    return _mk_hop(hop_num, ip, rtts, timeout=ip is None and not any(r is not None for r in rtts))


def parse_line(line: str, os_kind: Optional[str] = None) -> Optional[dict]:
    """Parse a single line of trace output into a normalized hop dict.

    Returns None if the line carries no hop data (banners, blank lines).
    Returns {"done": True} for the Windows "Trace complete." sentinel.
    """
    os_kind = os_kind or detect_os_kind()
    if os_kind == "windows":
        return _parse_windows_line(line)
    return _parse_unix_line(line)


# ---------------------------------------------------------------------------
# Streaming subprocess execution
# ---------------------------------------------------------------------------


async def _iter_subprocess_lines(cmd: list[str]) -> AsyncIterator[str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.DEVNULL,
    )
    assert proc.stdout is not None
    try:
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            yield _decode(raw)
    finally:
        if proc.returncode is None:
            proc.terminate()
        await proc.wait()


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "cp437", "cp850", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


async def stream_traceroute(target: str) -> AsyncIterator[dict]:
    """Run a real ICMP/UDP traceroute against `target`, yielding hop dicts
    as they arrive. `target` must already be validated (see validation.py).
    """
    os_kind = detect_os_kind()
    cmd = build_command(target, os_kind)
    async for raw_line in _iter_subprocess_lines(cmd):
        hop = parse_line(raw_line, os_kind)
        if hop is None:
            continue
        if hop.get("done"):
            return
        yield hop


# ---------------------------------------------------------------------------
# Optional TCP-mode traceroute (scapy), feature-flagged
# ---------------------------------------------------------------------------

try:
    from scapy.all import IP, TCP, sr1  # type: ignore

    SCAPY_AVAILABLE = True
except Exception:  # pragma: no cover - absence is the common case in CI
    SCAPY_AVAILABLE = False


class TcpModeUnavailable(RuntimeError):
    """Raised when TCP mode is requested but scapy/raw sockets aren't usable."""


async def stream_tcp_traceroute(target_ip: str, port: int = 443, max_hops: int = 30) -> AsyncIterator[dict]:
    """TCP SYN traceroute using scapy. Requires raw socket privileges:
    Administrator + Npcap on Windows, or root/CAP_NET_RAW on Linux/macOS.

    Never crashes the caller if scapy is missing or permissions are denied;
    raises TcpModeUnavailable with a clear message instead so the API layer
    can surface it as an `error` SSE event.
    """
    if not SCAPY_AVAILABLE:
        raise TcpModeUnavailable(
            "TCP mode requires the 'scapy' package, which is not installed. "
            "Run `pip install scapy` to enable it."
        )

    loop = asyncio.get_event_loop()

    def _probe(ttl: int):
        pkt = IP(dst=target_ip, ttl=ttl) / TCP(dport=port, flags="S")
        t0 = time.perf_counter()
        reply = sr1(pkt, timeout=1, verbose=0)
        rtt_ms = (time.perf_counter() - t0) * 1000
        return reply, rtt_ms

    for ttl in range(1, max_hops + 1):
        try:
            reply, rtt_ms = await loop.run_in_executor(None, _probe, ttl)
        except PermissionError as exc:
            raise TcpModeUnavailable(
                "Permission denied sending raw packets. On Windows, install "
                "Npcap (https://npcap.com) and run as Administrator. On "
                "Linux/macOS, run with sudo or grant the CAP_NET_RAW "
                "capability."
            ) from exc
        except OSError as exc:
            raise TcpModeUnavailable(
                f"TCP mode failed to send packets: {exc}. This usually means "
                "Npcap is missing (Windows) or the process lacks raw-socket "
                "privileges (Linux/macOS)."
            ) from exc

        if reply is None:
            yield _mk_hop(ttl, None, [None], timeout=True)
            continue

        ip = reply.src
        yield _mk_hop(ttl, ip, [rtt_ms], timeout=False)

        reached = ip == target_ip
        if reply.haslayer(TCP):
            flags = int(reply[TCP].flags)
            # SYN-ACK (0x12) or RST (0x14) from the target means we're done.
            if flags & 0x04 or (flags & 0x12) == 0x12:
                reached = reached or True
        if reached:
            return
