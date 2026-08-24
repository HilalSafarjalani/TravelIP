"""IP geolocation with classification, SQLite caching, and rate limiting.

Lookup pipeline for a batch of IPs:
  1. Classify each IP (private / cgnat / public / unknown). Only "public"
     IPs are ever sent to a geolocation provider.
  2. Private/cgnat IPs get a best-effort reverse DNS (PTR) lookup instead --
     no coordinates (there are none to find), but a hostname occasionally
     hints at a real location. Never cached: a private IP's meaning is
     specific to whatever network it was seen on.
  3. Check the SQLite cache (30-day TTL) for public IPs. Cache hits never
     touch the network.
  4. Remaining public IPs are looked up via ip-api.com's batch endpoint
     (up to 100 IPs per call, capped at 45 calls/min). Any IP the batch
     call fails to resolve falls back to ipwho.is, looked up individually.
  5. Fresh results are written back to the cache.
  6. Anything still without a location (private/cgnat, or a public IP both
     providers failed on) gets one last resort: parse its reverse-DNS
     hostname for an IATA airport code (see backend/airports.py) and use
     that city as an `inferred` location. Never overrides a real result,
     never cached (it's a guess, not a fact).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from collections import deque
from typing import Optional

import httpx

from backend.airports import find_airport_code_in_hostname
from backend.db import get_db

CACHE_TTL_SECONDS = 30 * 24 * 3600  # 30 days
IP_API_BATCH_URL = "http://ip-api.com/batch"
IP_API_SELF_URL = "http://ip-api.com/json/"
IPWHO_URL_TMPL = "https://ipwho.is/{ip}"
IP_API_FIELDS = "status,message,country,countryCode,city,lat,lon,isp,org,as,asname,reverse,query"
BATCH_CHUNK_SIZE = 100
RATE_LIMIT_CALLS = 45
RATE_LIMIT_WINDOW_S = 60.0

_CGNAT_V4 = ipaddress.ip_network("100.64.0.0/10")
REVERSE_DNS_TIMEOUT_S = 1.5


def classify_ip(ip_str: str) -> str:
    """Classify an IP into one of: private, cgnat, public, unknown.

    "private" folds together RFC1918, loopback, link-local, and other
    non-globally-routable/reserved ranges -- anything that is definitionally
    not geolocatable because it doesn't identify a place on the public
    internet. CGNAT (100.64.0.0/10) is called out separately per spec even
    though it behaves the same way (not geolocated), since it's a distinct
    and increasingly common case (carrier-grade NAT on cellular/ISP links).
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return "unknown"

    if isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT_V4:
        return "cgnat"

    if ip.is_loopback or ip.is_link_local or ip.is_private or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
        return "private"

    return "public"


def _empty_geo(ip: str, kind: str, source: str = "none", reverse: Optional[str] = None) -> dict:
    return {
        "ip": ip,
        "kind": kind,
        "country": None,
        "country_code": None,
        "city": None,
        "lat": None,
        "lon": None,
        "isp": None,
        "org": None,
        "asn": None,
        "as_name": None,
        "reverse": reverse,
        "source": source,
        "inferred": False,
    }


class _RateLimiter:
    """Sliding-window limiter: at most `calls` calls per `window` seconds."""

    def __init__(self, calls: int, window: float):
        self.calls = calls
        self.window = window
        self._times: deque[float] = deque()

    async def acquire(self) -> None:
        while True:
            now = time.monotonic()
            while self._times and now - self._times[0] > self.window:
                self._times.popleft()
            if len(self._times) < self.calls:
                self._times.append(now)
                return
            wait = self.window - (now - self._times[0]) + 0.05
            await asyncio.sleep(max(wait, 0.05))


class GeoService:
    def __init__(self, client: httpx.AsyncClient):
        self._client = client
        self._limiter = _RateLimiter(RATE_LIMIT_CALLS, RATE_LIMIT_WINDOW_S)

    # -- cache -------------------------------------------------------------

    async def _cache_get(self, ips: list[str]) -> dict[str, dict]:
        if not ips:
            return {}
        db = await get_db()
        placeholders = ",".join("?" for _ in ips)
        cutoff = time.time() - CACHE_TTL_SECONDS
        query = (
            f"SELECT ip, kind, country, country_code, city, lat, lon, isp, org, "
            f"asn, as_name, reverse, source FROM geo_cache "
            f"WHERE ip IN ({placeholders}) AND fetched_at >= ?"
        )
        rows = await db.execute_fetchall(query, (*ips, cutoff))
        result = {}
        for row in rows:
            (ip, kind, country, cc, city, lat, lon, isp, org, asn, as_name, reverse, source) = row
            result[ip] = {
                "ip": ip,
                "kind": kind,
                "country": country,
                "country_code": cc,
                "city": city,
                "lat": lat,
                "lon": lon,
                "isp": isp,
                "org": org,
                "asn": asn,
                "as_name": as_name,
                "reverse": reverse,
                "source": "cache",
            }
        return result

    async def _cache_put(self, results: list[dict]) -> None:
        if not results:
            return
        db = await get_db()
        now = time.time()
        await db.executemany(
            """
            INSERT INTO geo_cache
                (ip, kind, country, country_code, city, lat, lon, isp, org, asn, as_name, reverse, source, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ip) DO UPDATE SET
                kind=excluded.kind, country=excluded.country, country_code=excluded.country_code,
                city=excluded.city, lat=excluded.lat, lon=excluded.lon, isp=excluded.isp,
                org=excluded.org, asn=excluded.asn, as_name=excluded.as_name,
                reverse=excluded.reverse, source=excluded.source, fetched_at=excluded.fetched_at
            """,
            [
                (
                    r["ip"], r["kind"], r["country"], r["country_code"], r["city"],
                    r["lat"], r["lon"], r["isp"], r["org"], r["asn"], r["as_name"],
                    r["reverse"], r["source"], now,
                )
                for r in results
            ],
        )
        await db.commit()

    # -- reverse DNS (private/cgnat hops only; public hops get it from the
    #    geolocation provider's own "reverse" field instead) -----------------

    async def _reverse_dns(self, ip: str) -> Optional[str]:
        """Best-effort PTR lookup for a non-public IP. Most private-network
        addresses have no reverse record at all, but some ISP-internal
        backbone hops do, and a hostname there can hint at a real location
        (e.g. "par1-core.example.net") without us inventing coordinates.
        Never raises; a failed or slow (>REVERSE_DNS_TIMEOUT_S) lookup just
        yields None.
        """
        loop = asyncio.get_event_loop()
        try:
            host, _aliases, _addrs = await asyncio.wait_for(
                loop.run_in_executor(None, socket.gethostbyaddr, ip),
                timeout=REVERSE_DNS_TIMEOUT_S,
            )
            return host
        except Exception:
            return None

    def _apply_hostname_inference(self, entry: dict, host: Optional[str]) -> dict:
        """Last resort: if `entry` still has no coordinates, try to pull a
        city out of `host` via an IATA airport code. Never overrides a
        result that already has a real lat/lon.
        """
        if entry.get("lat") is not None:
            return entry
        hit = find_airport_code_in_hostname(host)
        if not hit:
            return entry
        return {
            **entry,
            "country": hit["country"],
            "country_code": hit["country_code"],
            "city": hit["city"],
            "lat": hit["lat"],
            "lon": hit["lon"],
            "inferred": True,
            "source": "hostname-inference",
        }

    # -- providers -----------------------------------------------------------

    async def _batch_lookup(self, ips: list[str]) -> dict[str, dict]:
        """Call ip-api.com/batch for up to 100 IPs. Returns dict of successes
        only; IPs missing from the return dict need the fallback provider.
        """
        out: dict[str, dict] = {}
        for i in range(0, len(ips), BATCH_CHUNK_SIZE):
            chunk = ips[i : i + BATCH_CHUNK_SIZE]
            await self._limiter.acquire()
            try:
                resp = await self._client.post(
                    IP_API_BATCH_URL,
                    params={"fields": IP_API_FIELDS},
                    json=chunk,
                    timeout=10.0,
                )
                resp.raise_for_status()
                data = resp.json()
            except (httpx.HTTPError, ValueError):
                continue  # every IP in this chunk falls through to fallback
            for entry in data:
                ip = entry.get("query")
                if not ip or entry.get("status") != "success":
                    continue
                out[ip] = {
                    "ip": ip,
                    "kind": "public",
                    "country": entry.get("country"),
                    "country_code": entry.get("countryCode"),
                    "city": entry.get("city"),
                    "lat": entry.get("lat"),
                    "lon": entry.get("lon"),
                    "isp": entry.get("isp"),
                    "org": entry.get("org"),
                    "asn": entry.get("as"),
                    "as_name": entry.get("asname"),
                    "reverse": entry.get("reverse") or None,
                    "source": "ip-api",
                    "inferred": False,
                }
        return out

    async def _fallback_lookup_one(self, ip: str) -> Optional[dict]:
        try:
            resp = await self._client.get(IPWHO_URL_TMPL.format(ip=ip), timeout=10.0)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            return None
        if not data.get("success", False):
            return None
        conn = data.get("connection") or {}
        asn = conn.get("asn")
        return {
            "ip": ip,
            "kind": "public",
            "country": data.get("country"),
            "country_code": data.get("country_code"),
            "city": data.get("city"),
            "lat": data.get("latitude"),
            "lon": data.get("longitude"),
            "isp": conn.get("isp"),
            "org": conn.get("org"),
            "asn": f"AS{asn}" if asn else None,
            "as_name": conn.get("org"),
            "reverse": None,
            "source": "ipwho.is",
            "inferred": False,
        }

    # -- public API ------------------------------------------------------

    async def lookup_many(self, ips: list[str]) -> dict[str, dict]:
        """Resolve geolocation for a list of IPs. Never raises; unresolvable
        IPs come back with lat/lon None and source reflecting what was tried.
        """
        result: dict[str, dict] = {}
        public_ips: list[str] = []
        dns_candidates: list[str] = []
        seen: set[str] = set()

        for ip in ips:
            if ip in seen:
                continue
            seen.add(ip)
            kind = classify_ip(ip)
            if kind == "public":
                public_ips.append(ip)
            else:
                result[ip] = _empty_geo(ip, kind, source="local")
                if kind in ("private", "cgnat"):
                    dns_candidates.append(ip)

        if dns_candidates:
            reverses = await asyncio.gather(*(self._reverse_dns(ip) for ip in dns_candidates))
            for ip, host in zip(dns_candidates, reverses):
                result[ip]["reverse"] = host
                result[ip] = self._apply_hostname_inference(result[ip], host)

        cached = await self._cache_get(public_ips)
        result.update(cached)

        missing = [ip for ip in public_ips if ip not in cached]
        if missing:
            batch_hits = await self._batch_lookup(missing)
            result.update(batch_hits)
            await self._cache_put(list(batch_hits.values()))

            still_missing = [ip for ip in missing if ip not in batch_hits]
            fallback_results = []
            for ip in still_missing:
                hit = await self._fallback_lookup_one(ip)
                if hit:
                    result[ip] = hit
                    fallback_results.append(hit)
                else:
                    entry = _empty_geo(ip, "public", source="failed")
                    host = await self._reverse_dns(ip)
                    entry["reverse"] = host
                    result[ip] = self._apply_hostname_inference(entry, host)
            await self._cache_put(fallback_results)

        return result

    async def lookup_one(self, ip: str) -> dict:
        result = await self.lookup_many([ip])
        return result[ip]

    async def get_self_location(self) -> dict:
        """One-shot lookup of this machine's own public IP + location, used
        as the trace origin (hop 0). Never cached in geo_cache (it's a
        single startup call, not part of the per-trace hop pipeline), and
        never fatal -- falls back to an "unknown" origin if offline.
        """
        try:
            resp = await self._client.get(
                IP_API_SELF_URL,
                params={"fields": IP_API_FIELDS},
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            return _empty_geo("unknown", "unknown", source="failed")

        if data.get("status") != "success":
            return _empty_geo("unknown", "unknown", source="failed")

        ip = data.get("query") or "unknown"
        return {
            "ip": ip,
            "kind": "public",
            "country": data.get("country"),
            "country_code": data.get("countryCode"),
            "city": data.get("city"),
            "lat": data.get("lat"),
            "lon": data.get("lon"),
            "isp": data.get("isp"),
            "org": data.get("org"),
            "asn": data.get("as"),
            "as_name": data.get("asname"),
            "reverse": data.get("reverse") or None,
            "source": "ip-api",
            "inferred": False,
        }
