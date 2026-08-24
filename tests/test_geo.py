"""Tests for backend.geo: IP classification and the SQLite geo cache.

Network calls are stubbed with a fake httpx-like client so these tests never
touch the real internet; the cache logic itself (SQLite reads/writes, TTL,
cold-vs-warm behavior) is exercised for real against a temp SQLite file.
"""

from __future__ import annotations

import socket

import pytest

from backend.geo import GeoService, classify_ip


@pytest.fixture(autouse=True)
def no_real_dns(monkeypatch):
    """Reverse DNS (used for private/cgnat hops) defaults to failing
    instantly in every test here -- none of our test IPs have real PTR
    records, and these tests must stay offline regardless. Individual tests
    can still override this with their own monkeypatch.setattr call.
    """

    def _fail(_ip):
        raise socket.herror("no reverse record")

    monkeypatch.setattr(socket, "gethostbyaddr", _fail)


# ---------------------------------------------------------------------------
# classify_ip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ip,expected",
    [
        ("10.0.0.1", "private"),
        ("172.16.5.5", "private"),
        ("172.31.255.255", "private"),
        ("192.168.1.1", "private"),
        ("127.0.0.1", "private"),
        ("169.254.1.1", "private"),
        ("::1", "private"),
        ("fe80::1", "private"),
        ("fc00::1", "private"),
        ("100.64.0.1", "cgnat"),
        ("100.127.255.255", "cgnat"),
        ("100.63.255.255", "public"),  # just outside the CGNAT block
        ("8.8.8.8", "public"),
        ("2001:4860:4860::8888", "public"),
        ("not-an-ip", "unknown"),
        ("", "unknown"),
    ],
)
def test_classify_ip(ip, expected):
    assert classify_ip(ip) == expected


# ---------------------------------------------------------------------------
# Fake httpx client
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeClient:
    """Records call counts so tests can assert cold vs warm cache behavior."""

    def __init__(self, batch_payload_factory=None, fallback_payload_factory=None):
        self.batch_calls = 0
        self.fallback_calls = 0
        self._batch_payload_factory = batch_payload_factory
        self._fallback_payload_factory = fallback_payload_factory

    async def post(self, url, params=None, json=None, timeout=None):
        self.batch_calls += 1
        payload = self._batch_payload_factory(json) if self._batch_payload_factory else []
        return FakeResponse(payload)

    async def get(self, url, params=None, timeout=None):
        self.fallback_calls += 1
        payload = self._fallback_payload_factory(url) if self._fallback_payload_factory else {"success": False}
        return FakeResponse(payload)


def _ok_batch_entry(ip: str) -> dict:
    return {
        "status": "success",
        "query": ip,
        "country": "United States",
        "countryCode": "US",
        "city": "Mountain View",
        "lat": 37.42,
        "lon": -122.08,
        "isp": "Google LLC",
        "org": "Google LLC",
        "as": "AS15169 Google LLC",
        "asname": "GOOGLE",
        "reverse": "dns.google",
    }


# ---------------------------------------------------------------------------
# Cache: cold vs warm (acceptance test 5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cold_cache_hits_network_warm_cache_does_not(temp_db):
    client = FakeClient(batch_payload_factory=lambda ips: [_ok_batch_entry(ip) for ip in ips])

    geo1 = GeoService(client)
    cold = await geo1.lookup_many(["8.8.8.8"])
    assert client.batch_calls == 1
    assert cold["8.8.8.8"]["source"] == "ip-api"
    assert cold["8.8.8.8"]["city"] == "Mountain View"
    assert cold["8.8.8.8"]["kind"] == "public"

    # Fresh GeoService instance (as would happen on a new request), same
    # underlying SQLite cache file -> must be served entirely from cache.
    geo2 = GeoService(client)
    warm = await geo2.lookup_many(["8.8.8.8"])
    assert client.batch_calls == 1  # no new network call
    assert warm["8.8.8.8"]["source"] == "cache"
    assert warm["8.8.8.8"]["city"] == "Mountain View"


@pytest.mark.asyncio
async def test_private_and_cgnat_ips_never_hit_network(temp_db):
    client = FakeClient(batch_payload_factory=lambda ips: [_ok_batch_entry(ip) for ip in ips])
    geo = GeoService(client)

    result = await geo.lookup_many(["192.168.1.1", "100.64.0.5", "127.0.0.1"])

    assert client.batch_calls == 0
    assert result["192.168.1.1"]["kind"] == "private"
    assert result["192.168.1.1"]["lat"] is None
    assert result["100.64.0.5"]["kind"] == "cgnat"
    assert result["127.0.0.1"]["kind"] == "private"


@pytest.mark.asyncio
async def test_fallback_used_when_batch_fails_for_an_ip(temp_db):
    def batch_factory(ips):
        # simulate ip-api failing to resolve this particular IP
        return [{"status": "fail", "message": "reserved range", "query": ip} for ip in ips]

    def fallback_factory(url):
        return {
            "success": True,
            "country": "United States",
            "country_code": "US",
            "city": "Ashburn",
            "latitude": 39.04,
            "longitude": -77.48,
            "connection": {"isp": "Amazon", "org": "AWS", "asn": 14618},
        }

    client = FakeClient(batch_payload_factory=batch_factory, fallback_payload_factory=fallback_factory)
    geo = GeoService(client)

    result = await geo.lookup_many(["1.2.3.4"])
    assert client.batch_calls == 1
    assert client.fallback_calls == 1
    assert result["1.2.3.4"]["source"] == "ipwho.is"
    assert result["1.2.3.4"]["city"] == "Ashburn"
    assert result["1.2.3.4"]["asn"] == "AS14618"


@pytest.mark.asyncio
async def test_private_ip_gets_reverse_dns_hostname_when_available(temp_db, monkeypatch):
    monkeypatch.setattr(socket, "gethostbyaddr", lambda ip: ("core1.par1.example.net", [], [ip]))
    client = FakeClient()
    geo = GeoService(client)

    result = await geo.lookup_many(["10.20.20.1"])

    assert result["10.20.20.1"]["kind"] == "private"
    assert result["10.20.20.1"]["reverse"] == "core1.par1.example.net"
    # Reverse DNS gives a hint, never coordinates -- pinning a location is
    # main.py's job (hop 1 only), not something geo.py does on its own.
    assert result["10.20.20.1"]["lat"] is None
    assert result["10.20.20.1"]["inferred"] is False


@pytest.mark.asyncio
async def test_private_ip_reverse_dns_failure_is_swallowed(temp_db):
    client = FakeClient()
    geo = GeoService(client)

    result = await geo.lookup_many(["192.168.1.1"])

    assert result["192.168.1.1"]["reverse"] is None


@pytest.mark.asyncio
async def test_mixed_public_and_private_batch(temp_db):
    client = FakeClient(batch_payload_factory=lambda ips: [_ok_batch_entry(ip) for ip in ips])
    geo = GeoService(client)

    result = await geo.lookup_many(["8.8.8.8", "10.0.0.1", "8.8.8.8"])  # dup intentional

    assert client.batch_calls == 1
    assert result["8.8.8.8"]["kind"] == "public"
    assert result["10.0.0.1"]["kind"] == "private"
    assert len(result) == 2
