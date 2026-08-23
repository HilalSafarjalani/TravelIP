"""Integration tests for the FastAPI app: SSE contract, validation, history.

The real tracert/traceroute subprocess and real geolocation network calls are
stubbed out here (they're exercised for real in tests/test_tracer.py and via
manual live verification) so these tests run fast and offline, focused on
the API layer: event framing, input validation, and history persistence.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import backend.main as main_module


class FakeGeo:
    def __init__(self, *_args, **_kwargs):
        pass

    async def get_self_location(self):
        return {
            "ip": "203.0.113.9", "kind": "public", "country": "United States",
            "country_code": "US", "city": "Somewhere", "lat": 10.0, "lon": 20.0,
            "isp": "Test ISP", "org": "Test Org", "asn": "AS1", "as_name": "TEST",
            "reverse": None, "source": "ip-api",
        }

    async def lookup_one(self, ip):
        return {
            "ip": ip, "kind": "public", "country": "United States", "country_code": "US",
            "city": "Testville", "lat": 1.0, "lon": 2.0, "isp": "ISP", "org": "Org",
            "asn": "AS2", "as_name": "ORG", "reverse": None, "source": "ip-api",
        }


async def _fake_stream_traceroute(target):
    yield {"hop": 1, "ip": "192.168.1.1", "rtts": [1.0, 1.0, 1.0], "avg_rtt": 1.0, "timeout": False}
    yield {"hop": 2, "ip": None, "rtts": [None, None, None], "avg_rtt": None, "timeout": True}
    yield {"hop": 3, "ip": "8.8.8.8", "rtts": [10.0, 11.0, 10.0], "avg_rtt": 10.333, "timeout": False}


@pytest.fixture
def client(temp_db, monkeypatch):
    monkeypatch.setattr(main_module, "GeoService", FakeGeo)
    monkeypatch.setattr(main_module, "stream_traceroute", _fake_stream_traceroute)
    with TestClient(main_module.app) as c:
        yield c


# ---------------------------------------------------------------------------
# Validation (acceptance test 4)
# ---------------------------------------------------------------------------


def test_shell_injection_attempt_rejected(client):
    resp = client.get("/api/trace", params={"target": "google.com; rm -rf /"})
    assert resp.status_code == 400
    assert "error" in resp.json()


def test_flag_injection_attempt_rejected(client):
    resp = client.get("/api/trace", params={"target": "-h"})
    assert resp.status_code == 400


def test_invalid_mode_rejected(client):
    resp = client.get("/api/trace", params={"target": "8.8.8.8", "mode": "bogus"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# SSE contract (acceptance tests 2 & 3, with a timeout hop mixed in)
# ---------------------------------------------------------------------------


def test_trace_streams_all_event_types_including_a_real_timeout(client):
    resp = client.get("/api/trace", params={"target": "8.8.8.8"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    body = resp.text
    assert "event: origin" in body
    assert "event: hop" in body
    assert "event: geo" in body
    assert "event: done" in body

    # hop 2 is a genuine timeout: ip null, timeout true -- never fabricated
    assert '"ip": null' in body
    assert '"timeout": true' in body

    # the two successful hops each got a follow-up geo event
    assert body.count("event: geo") == 2


def test_valid_target_forms_accepted(client):
    for target in ("8.8.8.8", "dns.google", "2001:4860:4860::8888"):
        resp = client.get("/api/trace", params={"target": target})
        assert resp.status_code == 200, target


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


def test_history_empty_initially(client):
    resp = client.get("/api/history")
    assert resp.status_code == 200
    assert resp.json() == []


def test_history_records_completed_trace(client):
    client.get("/api/trace", params={"target": "8.8.8.8"})
    resp = client.get("/api/history")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["target"] == "8.8.8.8"
    assert data[0]["mode"] == "icmp"
    assert data[0]["hop_count"] == 3
    assert data[0]["origin"]["city"] == "Somewhere"


def test_history_caps_at_20_most_recent(client):
    for _ in range(22):
        client.get("/api/trace", params={"target": "8.8.8.8"})
    resp = client.get("/api/history")
    data = resp.json()
    assert len(data) == 20
