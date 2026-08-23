import type { DoneInfo, ErrorInfo, GeoData, HopData, OriginData } from "./types";

export interface TraceHandlers {
  onOrigin: (origin: OriginData) => void;
  onHop: (hop: HopData) => void;
  onGeo: (geo: GeoData) => void;
  onDone: (info: DoneInfo) => void;
  onError: (info: ErrorInfo) => void;
}

/**
 * Opens the SSE trace stream and dispatches to typed handlers.
 *
 * EventSource's native connection-level "error" event and our server's
 * explicit `event: error` payload both surface as an event of type
 * "error" -- they're told apart by whether a `.data` payload is present.
 */
export function startTrace(target: string, mode: "icmp" | "tcp", handlers: TraceHandlers): EventSource {
  const url = `/api/trace?target=${encodeURIComponent(target)}&mode=${encodeURIComponent(mode)}`;
  const es = new EventSource(url);

  es.addEventListener("origin", (e) => {
    handlers.onOrigin(JSON.parse((e as MessageEvent).data));
  });

  es.addEventListener("hop", (e) => {
    handlers.onHop(JSON.parse((e as MessageEvent).data));
  });

  es.addEventListener("geo", (e) => {
    handlers.onGeo(JSON.parse((e as MessageEvent).data));
  });

  es.addEventListener("done", (e) => {
    handlers.onDone(JSON.parse((e as MessageEvent).data));
    es.close();
  });

  es.addEventListener("error", (e) => {
    const me = e as MessageEvent;
    if (me.data) {
      handlers.onError(JSON.parse(me.data));
    } else {
      handlers.onError({ message: "Connection to server lost." });
      es.close();
    }
  });

  return es;
}
