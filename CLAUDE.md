# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

TravelIP is a local-only traceroute visualizer. The backend runs a real OS
traceroute (`tracert`/`traceroute`) against a target, geolocates each hop,
and streams results over SSE. The frontend renders the path as an animated
arc on a 3D MapLibre globe, hop by hop, as data arrives.

This app is designed to run 100% locally and bind to `127.0.0.1` only —
never change host binding to `0.0.0.0` or add auth/remote-access features
without being asked; it assumes a trusted single-user local machine.

## Commands

Run the app (builds the frontend if `frontend/dist` is missing, then starts
the backend, which serves the built frontend as static files):

```powershell
./run.ps1      # Windows
```
```bash
./run.sh       # Linux/macOS
```

Manual equivalent:

```bash
cd frontend && npm install && npm run build   # outputs to frontend/dist
python -m backend.main                        # serves dist/ + API on :8000
```

Frontend dev loop (Vite dev server on :5173, proxies `/api` to :8000 — start
the backend separately first):

```bash
cd frontend && npm run dev
```

Backend tests:

```bash
python -m pytest              # all tests
python -m pytest tests/test_tracer.py            # one file
python -m pytest tests/test_tracer.py::test_name # one test
```

`pytest.ini` sets `pythonpath = .` and `asyncio_mode = auto` — async test
functions don't need `@pytest.mark.asyncio`.

There is no frontend test suite. `frontend/m4_screenshot.mjs` is an ad hoc
Playwright script (not a test) used to visually QA the trace animation: it
starts a trace against `8.8.8.8` against a running server at
`http://127.0.0.1:8000/`, screenshots it repeatedly, and dumps any console/page
errors as JSON. Run it with `node m4_screenshot.mjs` from `frontend/` while
the backend is serving the built frontend.

TypeScript type-checking happens as part of `npm run build` (`tsc -b`), there
is no separate lint/typecheck command.

## Architecture

**Backend** (`backend/`, FastAPI, all async):

- `main.py` — the only HTTP surface. `GET /api/trace` streams Server-Sent
  Events; `GET /api/history` returns the last 20 traces from SQLite. Also
  mounts `frontend/dist` as static files at `/` (falls back to a JSON hint
  if the frontend hasn't been built yet).

  SSE event contract on `GET /api/trace?target=...&mode=icmp|tcp`:
  - `event: origin` — once, at the start: this machine's own public IP +
    geolocation (computed once at app startup, in `lifespan`, and reused
    across all requests).
  - `event: hop` — once per hop, as soon as the traceroute subprocess line
    is parsed (`HopData`: hop number, ip, per-probe rtts, avg_rtt, timeout).
    No geo info yet.
  - `event: geo` — once per hop, when that hop's IP finishes geolocating
    (fired from a concurrent task per hop, so `geo` events can arrive
    out of order relative to `hop`/other hops). Hop 1 is special-cased in
    `_geo_lookup_and_queue`: if it's not a public IP (almost always true —
    it's your own router), its lat/lon/city/etc are pinned to the trace
    origin's already-known location and `inferred: true` is set on the geo
    payload, so the frontend can show/plot it while making clear it's an
    assumption, not a measurement. Every other private/cgnat hop instead
    gets a best-effort reverse DNS lookup (see `geo.py`) — no coordinates,
    but `reverse` may carry a hostname hint.
  - `event: done` — once, when the trace subprocess exits and all pending
    geo lookups have settled. Trace history is saved to SQLite just before
    this fires.
  - `event: error` — zero or more times: either a recoverable problem (TCP
    mode unavailable — e.g. no scapy, no raw-socket privilege) or a fatal
    one (trace tool not found on PATH), always followed by `done`.

  The frontend's `EventSource` in `frontend/src/api.ts` is the source of
  truth for how these are consumed client-side.

- `tracer.py` — spawns the OS-native trace tool as an asyncio subprocess and
  parses its stdout line-by-line into hop dicts, streaming as it goes
  (never buffers the whole trace). Separate parsers for Windows `tracert`
  output and Unix `traceroute` output — the two tools have meaningfully
  different line formats and failure modes (`* * *` vs "Request timed out"),
  see the regexes and comments at the top of the file before touching either
  parser. Never fabricates a hop's IP or RTT: a timeout is `ip=None,
  timeout=True`.

  TCP mode (`stream_tcp_traceroute`) is a from-scratch scapy-based SYN
  traceroute, feature-flagged on `scapy` being importable. It requires raw
  socket privileges (Administrator + Npcap on Windows, root/CAP_NET_RAW on
  Linux/macOS) and raises `TcpModeUnavailable` — never crashes the request —
  if scapy is missing or permission is denied; `main.py` turns that into an
  `error` SSE event.

- `geo.py` — IP geolocation pipeline: classify each IP as
  `private`/`cgnat`/`public`/`unknown` first (only `public` IPs ever hit a
  geo provider — see `classify_ip`'s docstring for why CGNAT is called out
  separately from private). Private/cgnat IPs instead get a best-effort
  reverse DNS (PTR) lookup (`_reverse_dns`, 1.5s timeout, never cached —
  a private IP's meaning is specific to whatever network it was seen on).
  Public IPs check the SQLite cache (30-day TTL, cache hits never touch the
  network), batch-lookup the rest via ip-api.com (rate limited to 45
  calls/min), falling back to ipwho.is per-IP for anything the batch call
  missed. Whatever still has no coordinates after all that (private/cgnat,
  or a public IP both providers failed on) gets one last resort via
  `_apply_hostname_inference`: parse its reverse-DNS hostname for an IATA
  airport code (`backend/airports.py`) and use that city, marked
  `inferred: true`, `source: "hostname-inference"`. Never overrides a real
  result and is never cached.

- `airports.py` — the IATA-code table and hostname parser behind the
  hostname-inference fallback above. Deliberately a curated subset (major
  hub metros only, not all ~9000 IATA codes) and deliberately excludes a
  couple of real codes that collide with common networking terms (see the
  comment above `_LABEL_RE`) — read that comment before adding codes.

- `validation.py` — validates/normalizes the user-supplied target before
  it's used to build subprocess argv. Subprocess is invoked with
  `shell=False` (so no shell-injection risk from argv content itself), but
  validation is still strict: reject shell metacharacters, whitespace,
  leading `-` (which `tracert`/`traceroute` would parse as a flag), and
  anything that isn't a syntactically valid IP or hostname.

- `db.py` — single shared `aiosqlite` connection (WAL mode), opened once at
  startup, holding the `geo_cache` and `trace_history` tables.

**Frontend** (`frontend/src/`, TypeScript + Vite, no framework):

- `main.ts` — wires up the DOM (`#target-input`, `#go-btn`, `#hop-panel`,
  `#status-bar`, etc.), owns the `Map<number, HopRecord>` of hops for the
  in-progress trace, and is the only place that talks to `api.ts` and
  drives `AnimationEngine`.
- `api.ts` — thin typed wrapper around `EventSource` for `/api/trace`,
  dispatching to handler callbacks per SSE event type. Note the comment
  about how a native connection-level EventSource error and the server's
  explicit `event: error` payload are told apart (presence of `.data`).
- `globe.ts` — owns the MapLibre map instance + deck.gl `MapboxOverlay`
  (globe projection, atmosphere, auto-rotate, camera). Deliberately dumb:
  it just renders whatever layer array it's handed. All layer composition
  (nodes, arcs, the animated "packet" traveling along the arc, ripple fx)
  lives in `animator.ts`.
- `animator.ts` (`AnimationEngine`) — the animation state machine. Hops
  arrive asynchronously (and out of order for `geo` relative to `hop`); this
  queues them into "beats" that play sequentially — either a `move` to a
  newly-geolocated hop, or a `ghost` beat that fast-forwards through a run
  of ungeolocatable hops (private/timeout) between two mappable ones — so
  the camera/arc always advances hop-by-hop even though geo data trickles
  in. `engine.onArrive` fires when a beat lands, which is what triggers the
  floating label card in `main.ts`.
- `geoutil.ts` — pure math/formatting helpers used by the animator (great
  circle paths, haversine distance, easing, dash-segment generation for the
  arc, flag emoji from country code) — no DOM/map state.
- `types.ts` — the TypeScript mirror of the backend's SSE payload shapes
  (`HopData`, `GeoData`, `OriginData`, etc.). Keep in sync with `main.py`'s
  event contract and `geo.py`'s output fields by hand — there's no shared
  schema between backend and frontend.

`vite.config.ts` proxies `/api/*` to `http://127.0.0.1:8000` for the dev
server; in production the backend serves the built frontend directly from
the same origin, so no proxy/CORS is needed there (the CORS middleware in
`main.py` only allows the Vite dev origins).
