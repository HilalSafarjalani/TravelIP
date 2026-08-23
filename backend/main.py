"""FastAPI app: SSE trace endpoint, trace history, static frontend serving.

Binds to 127.0.0.1 only -- see run.ps1 / run.sh / the __main__ block below,
all of which pass host="127.0.0.1" to uvicorn.

SSE event contract on GET /api/trace (see CLAUDE.md for the authoritative
version):
  event: origin   -- once, at the start: this machine's public IP + location
  event: hop       -- once per traceroute hop, as it arrives (no geo yet)
  event: geo        -- once per hop, when that hop's IP finishes geolocating
  event: done      -- once, when the trace subprocess exits
  event: error   -- zero or more times: a recoverable problem (e.g. TCP mode
                     unavailable) or a fatal one, always followed by `done`
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from backend.db import close_db, get_db
from backend.geo import GeoService
from backend.tracer import TcpModeUnavailable, stream_tcp_traceroute, stream_traceroute
from backend.validation import InvalidTargetError, validate_target

FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = httpx.AsyncClient(headers={"User-Agent": "TravelIP/1.0 (local)"})
    app.state.http_client = client
    app.state.geo = GeoService(client)
    await get_db()

    try:
        app.state.origin = await app.state.geo.get_self_location()
    except Exception:
        app.state.origin = None

    yield

    await client.aclose()
    await close_db()


app = FastAPI(title="TravelIP", lifespan=lifespan)

# Dev convenience: allow the Vite dev server to call the API before the
# frontend is built. Loopback-only, matching the "runs 100% locally" rule.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _geo_lookup_and_queue(geo: GeoService, hop_num: int, ip: str, queue: "asyncio.Queue"):
    result = await geo.lookup_one(ip)
    await queue.put(("geo", {"hop": hop_num, **result}))


async def _save_history(target: str, mode: str, started_at: float, origin: Optional[dict], hops: list[dict]) -> None:
    db = await get_db()
    await db.execute(
        """
        INSERT INTO trace_history (target, mode, started_at, finished_at, hop_count, origin_json, hops_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            target,
            mode,
            started_at,
            time.time(),
            len(hops),
            json.dumps(origin) if origin is not None else None,
            json.dumps(hops),
        ),
    )
    await db.commit()


async def _trace_event_stream(app: FastAPI, target: str, mode: str):
    queue: asyncio.Queue = asyncio.Queue()
    geo: GeoService = app.state.geo
    origin = app.state.origin
    started_at = time.time()

    async def produce():
        hops: dict[int, dict] = {}
        geo_tasks: list[asyncio.Task] = []
        try:
            await queue.put(("origin", origin or {}))

            if mode == "tcp":
                hop_source = stream_tcp_traceroute(target)
            else:
                hop_source = stream_traceroute(target)

            try:
                async for hop in hop_source:
                    hops[hop["hop"]] = hop
                    await queue.put(("hop", hop))
                    if hop.get("ip"):
                        task = asyncio.create_task(_geo_lookup_and_queue(geo, hop["hop"], hop["ip"], queue))
                        geo_tasks.append(task)
            except TcpModeUnavailable as exc:
                await queue.put(("error", {"message": str(exc)}))
            except FileNotFoundError:
                tool = "tracert" if mode == "icmp" else "traceroute"
                await queue.put((
                    "error",
                    {"message": f"'{tool}' was not found on this system's PATH. It's required to run a trace."},
                ))

            if geo_tasks:
                await asyncio.gather(*geo_tasks, return_exceptions=True)

            await _save_history(target, mode, started_at, origin, list(hops.values()))
            await queue.put(("done", {"hop_count": len(hops)}))
        except Exception as exc:  # last-resort guard: never let the SSE stream hang
            await queue.put(("error", {"message": f"Unexpected error: {exc}"}))
            await queue.put(("done", {"hop_count": len(hops)}))
        finally:
            await queue.put((None, None))

    producer_task = asyncio.create_task(produce())
    try:
        while True:
            event, data = await queue.get()
            if event is None:
                break
            yield _sse(event, data)
    finally:
        producer_task.cancel()
        try:
            await producer_task
        except (asyncio.CancelledError, Exception):
            pass


@app.get("/api/trace")
async def trace(target: str = Query(...), mode: str = Query("icmp")):
    if mode not in ("icmp", "tcp"):
        return JSONResponse(status_code=400, content={"error": "mode must be 'icmp' or 'tcp'"})

    try:
        clean_target = validate_target(target)
    except InvalidTargetError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    return StreamingResponse(
        _trace_event_stream(app, clean_target, mode),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/history")
async def history():
    db = await get_db()
    rows = await db.execute_fetchall(
        """
        SELECT id, target, mode, started_at, finished_at, hop_count, origin_json, hops_json
        FROM trace_history ORDER BY id DESC LIMIT 20
        """
    )
    out = []
    for row in rows:
        (id_, target, mode, started_at, finished_at, hop_count, origin_json, hops_json) = row
        out.append({
            "id": id_,
            "target": target,
            "mode": mode,
            "started_at": started_at,
            "finished_at": finished_at,
            "hop_count": hop_count,
            "origin": json.loads(origin_json) if origin_json else None,
            "hops": json.loads(hops_json) if hops_json else [],
        })
    return out


if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
else:
    @app.get("/")
    async def root_placeholder():
        return JSONResponse({
            "status": "backend running, frontend not built yet",
            "hint": "cd frontend && npm install && npm run build",
        })


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=False)
