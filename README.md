# TravelIP

A local-only traceroute visualizer. The backend runs a real OS traceroute
against a target, geolocates each hop, and streams results over SSE. The
frontend renders the path as an animated arc on a 3D globe, hop by hop, as
data arrives.

This app is designed to run entirely on your own machine and binds to
`127.0.0.1` only — it is not meant to be exposed beyond loopback.

## Requirements

- Python 3.10+
- Node.js 18+ (for building the frontend)
- The OS-native trace tool on your `PATH`: `tracert` (Windows, built-in) or
  `traceroute` (Linux/macOS, install via your package manager if missing)

## Setup

```bash
# Backend
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate
pip install -r requirements.txt

# Frontend
cd frontend
npm install
npm run build
cd ..
```

## Running

```powershell
./run.ps1      # Windows
```
```bash
./run.sh        # Linux/macOS
```

Either script builds `frontend/dist` if it doesn't exist yet, then starts
the backend on `http://127.0.0.1:8000`, which serves the built frontend.
Open that URL, enter a target (IP or hostname), and click **Trace**.

Equivalent manual command: `python -m backend.main`.

### Frontend dev loop

For live-reload frontend work, run the backend and the Vite dev server
separately:

```bash
python -m backend.main       # terminal 1, serves the API on :8000
cd frontend && npm run dev   # terminal 2, dev server on :5173, proxies /api to :8000
```

## TCP mode

The ICMP/UDP trace (`tracert`/`traceroute`) works out of the box. TCP-mode
tracing additionally requires:

- The `scapy` package (included in `requirements.txt`)
- Raw socket privileges: [Npcap](https://npcap.com) + running as
  Administrator on Windows, or root/`CAP_NET_RAW` on Linux/macOS

If those aren't available, TCP mode reports a clear in-app error rather
than failing silently.

## Tests

```bash
python -m pytest
```

## More detail

See `CLAUDE.md` for the SSE event contract and a tour of the backend/
frontend architecture.
