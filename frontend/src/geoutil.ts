import * as turf from "@turf/turf";

export type LonLat = [number, number];
export type RGB = [number, number, number];

/** Great-circle path between two [lon, lat] points, as an array of [lon, lat] vertices. */
export function greatCirclePath(a: LonLat, b: LonLat, npoints = 128): LonLat[] {
  // turf.greatCircle degenerates for near-identical points (e.g. two hops
  // resolving to the same city) -- fall back to a trivial straight segment.
  if (Math.abs(a[0] - b[0]) < 1e-6 && Math.abs(a[1] - b[1]) < 1e-6) {
    return [a, b];
  }
  const line = turf.greatCircle(a, b, { npoints });
  const coords: LonLat[] = [];
  if (line.geometry.type === "LineString") {
    coords.push(...(line.geometry.coordinates as LonLat[]));
  } else {
    for (const part of line.geometry.coordinates as LonLat[][]) {
      coords.push(...part);
    }
  }
  return coords.length >= 2 ? coords : [a, b];
}

export function haversineKm(a: LonLat, b: LonLat): number {
  return turf.distance(a, b, { units: "kilometers" });
}

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

function lerpColor(a: RGB, b: RGB, t: number): RGB {
  return [Math.round(lerp(a[0], b[0], t)), Math.round(lerp(a[1], b[1], t)), Math.round(lerp(a[2], b[2], t))];
}

const COLOR_CYAN: RGB = [76, 224, 255];
const COLOR_AMBER: RGB = [255, 180, 84];
const COLOR_RED: RGB = [255, 70, 70];

/** Normalizes a per-hop latency delta (ms) onto 0..1 over a 0..220ms range. */
function normalizeDelta(deltaMs: number): number {
  return Math.min(Math.max(deltaMs, 0), 220) / 220;
}

/** Arc/trail color for a hop's delta RTT: cyan (fast) -> amber -> red (slow). */
export function colorForDelta(deltaMs: number): RGB {
  const t = normalizeDelta(deltaMs);
  return t < 0.5 ? lerpColor(COLOR_CYAN, COLOR_AMBER, t / 0.5) : lerpColor(COLOR_AMBER, COLOR_RED, (t - 0.5) / 0.5);
}

/** Packet travel duration for a hop's delta RTT, clamped to 400-1800ms per spec. */
export function durationForDelta(deltaMs: number): number {
  const t = normalizeDelta(deltaMs);
  return Math.round(400 + t * 1400);
}

/** Camera zoom that frames a hop of the given great-circle distance reasonably. */
export function zoomForDistanceKm(km: number): number {
  // Capped at 5.2 rather than a tighter close-up: a fresh tile cache needs to
  // fetch new tiles for a not-yet-visited region at high zoom, which can
  // leave a brief blank gap before they arrive. Staying a bit wider keeps
  // more of the view within already-loaded lower-zoom tiles.
  if (km < 40) return 5.2;
  const z = 5.2 - Math.log2(km / 40) * 0.7;
  return Math.min(5.2, Math.max(1.6, z));
}

/** ISO 3166-1 alpha-2 country code -> regional-indicator flag emoji. */
export function flagEmoji(countryCode: string | null | undefined): string {
  if (!countryCode || countryCode.length !== 2) return "🏳️";
  const upper = countryCode.toUpperCase();
  if (!/^[A-Z]{2}$/.test(upper)) return "🏳️";
  const points = [...upper].map((c) => 0x1f1e6 + c.charCodeAt(0) - 65);
  return String.fromCodePoint(...points);
}

/** Simple overshoot easing to approximate a spring "pop" on node arrival. */
export function easeOutBack(t: number): number {
  const c1 = 1.70158;
  const c3 = c1 + 1;
  const x = Math.min(Math.max(t, 0), 1);
  return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2);
}

export function easeInOutQuad(t: number): number {
  const x = Math.min(Math.max(t, 0), 1);
  return x < 0.5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2;
}

/** Splits a path into alternating visible/gap chunks to fake a dashed line (deck.gl PathLayer has no native dash support). */
export function dashSegments(path: LonLat[], dashLen = 3, gapLen = 2): LonLat[][] {
  const segments: LonLat[][] = [];
  let i = 0;
  let visible = true;
  while (i < path.length - 1) {
    const runLen = visible ? dashLen : gapLen;
    const end = Math.min(i + runLen, path.length - 1);
    if (visible) segments.push(path.slice(i, end + 1));
    i = end;
    visible = !visible;
  }
  return segments;
}
