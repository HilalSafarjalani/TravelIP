"""Input validation for user-supplied trace targets.

The target string is used to build an argument list for a subprocess
(tracert/traceroute) that is invoked with shell=False, so there is no shell
injection risk from argument content itself. Even so, we validate strictly:
we only want to hand the OS trace tool something that is actually a
syntactically valid IP address or hostname, and we reject anything that looks
like an attempt to smuggle shell metacharacters or extra arguments (e.g.
"google.com; rm -rf /", "--help", "-h 5 8.8.8.8").
"""

from __future__ import annotations

import ipaddress
import re

# Characters that have no legitimate place in a hostname/IP and are common
# shell/argument-injection vectors. Reject outright if any are present.
_FORBIDDEN_CHARS = re.compile(r'[;&|`$(){}<>"\'\\\s]')

# A single DNS label: 1-63 chars, alnum + hyphen, no leading/trailing hyphen.
_LABEL_RE = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")

MAX_HOSTNAME_LEN = 253


class InvalidTargetError(ValueError):
    """Raised when a user-supplied target fails validation."""


def validate_target(raw: str) -> str:
    """Validate and normalize a trace target.

    Returns the canonical string to pass as a subprocess argument (never
    through a shell). Raises InvalidTargetError with a human-readable reason
    if the input is not a valid IP address or hostname.
    """
    if raw is None:
        raise InvalidTargetError("Target is required.")

    candidate = raw.strip()

    if not candidate:
        raise InvalidTargetError("Target is required.")

    if len(candidate) > MAX_HOSTNAME_LEN:
        raise InvalidTargetError("Target is too long.")

    # Reject shell metacharacters / whitespace-embedded payloads up front,
    # before we even try to interpret the string as an IP or hostname.
    if _FORBIDDEN_CHARS.search(candidate):
        raise InvalidTargetError("Target contains disallowed characters.")

    # A leading '-' would be interpreted as a flag by tracert/traceroute even
    # in argv form; reject defensively.
    if candidate.startswith("-"):
        raise InvalidTargetError("Target must not start with '-'.")

    # Try IP address first (v4 or v6).
    try:
        ip = ipaddress.ip_address(candidate)
        return str(ip)
    except ValueError:
        pass

    # Fall back to hostname validation (allow IDNs via punycode encoding).
    try:
        ascii_host = candidate.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise InvalidTargetError("Target is not a valid hostname.") from exc

    if len(ascii_host) > MAX_HOSTNAME_LEN:
        raise InvalidTargetError("Target is too long.")

    labels = ascii_host.split(".")
    if len(labels) < 1 or any(not _LABEL_RE.match(label) for label in labels):
        raise InvalidTargetError("Target is not a valid hostname.")

    return candidate


def is_ip_address(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False
