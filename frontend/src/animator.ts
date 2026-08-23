import type { Layer } from "@deck.gl/core";
import { PathLayer, ScatterplotLayer, TextLayer } from "@deck.gl/layers";
import type { Globe } from "./globe";
import {
  colorForDelta,
  dashSegments,
  durationForDelta,
  easeInOutQuad,
  easeOutBack,
  greatCirclePath,
  haversineKm,
  zoomForDistanceKm,
  type LonLat,
  type RGB,
} from "./geoutil";
import type { HopRecord, OriginData } from "./types";

export interface AnimNode {
  id: string;
  lon: number;
  lat: number;
  city: string | null;
  country: string | null;
  countryCode: string | null;
  asn: string | null;
  isp: string | null;
  avgRtt: number | null;
  isOrigin: boolean;
  hopLabel: string;
  /** performance.now() timestamp this node "landed" -- drives pop/ripple fx. */
  arrivedAt: number;
}

interface SettledArc {
  id: string;
  path: LonLat[];
  color: RGB;
  dashed: boolean;
  ghostMidpoint?: LonLat;
}

type Beat = { kind: "move"; to: AnimNode; hop: HopRecord } | { kind: "ghost"; to: AnimNode; hops: HopRecord[] };

interface ActiveBeat {
  beat: Beat;
  path: LonLat[];
  color: RGB;
  startedAt: number;
  duration: number;
}

const RIPPLE_DURATION = 900;
const POP_DURATION = 500;
const ARRIVAL_PAUSE = 250;
const GHOST_BASE_DURATION = 550;
const GHOST_COLOR: RGB = [140, 150, 165];
const TRAIL_LEN = 34;

/**
 * Owns the hop-by-hop journey: a queue of "beats" fed live by the SSE
 * stream, played back sequentially at RTT-scaled durations (decoupled from
 * real network timing), each rendering a moving packet + comet trail,
 * camera choreography, and arrival effects (ripple + spring pop).
 */
export class AnimationEngine {
  private globe: Globe;
  private lastKnownNode: AnimNode | null = null;
  private lastRtt: number | null = null;
  private queue: Beat[] = [];
  private pendingTimeouts: HopRecord[] = [];
  private playing = false;
  private finished = false;
  private settledArcs: SettledArc[] = [];
  private settledNodes: AnimNode[] = [];
  private activeBeat: ActiveBeat | null = null;
  private tickHandle: number | null = null;
  private allPointsForFraming: LonLat[] = [];

  onArrive: ((node: AnimNode) => void) | null = null;
  onFinished: (() => void) | null = null;

  constructor(globe: Globe) {
    this.globe = globe;
  }

  /** Clears all journey state for a fresh trace. The render loop keeps running. */
  reset() {
    this.lastKnownNode = null;
    this.lastRtt = null;
    this.queue = [];
    this.pendingTimeouts = [];
    this.playing = false;
    this.finished = false;
    this.settledArcs = [];
    this.settledNodes = [];
    this.activeBeat = null;
    this.allPointsForFraming = [];
    this.globe.setOverlayLayers([]);
  }

  setOrigin(origin: OriginData) {
    if (origin.lat == null || origin.lon == null) return;
    const node: AnimNode = {
      id: "origin",
      lon: origin.lon,
      lat: origin.lat,
      city: origin.city,
      country: origin.country,
      countryCode: origin.country_code,
      asn: origin.asn,
      isp: origin.isp ?? origin.org,
      avgRtt: 0,
      isOrigin: true,
      hopLabel: "Origin (this machine)",
      arrivedAt: performance.now(),
    };
    this.lastKnownNode = node;
    this.settledNodes.push(node);
    this.allPointsForFraming.push([node.lon, node.lat]);
    this.ensureTicking();
    this.onArrive?.(node);
  }

  /** Call once a hop is fully resolved: timeout=true, or its geo has arrived. */
  submitHop(hop: HopRecord) {
    if (hop.timeout) {
      this.pendingTimeouts.push(hop);
      this.kick();
      return;
    }
    const geo = hop.geo;
    if (!geo || geo.kind !== "public" || geo.lat == null || geo.lon == null) {
      // Private/CGNAT/unknown: not mappable, not a timeout -- skip entirely
      // (still shown in the hop list, just never touches the globe).
      return;
    }
    const node: AnimNode = {
      id: `hop-${hop.hop}`,
      lon: geo.lon,
      lat: geo.lat,
      city: geo.city,
      country: geo.country,
      countryCode: geo.country_code,
      asn: geo.asn,
      isp: geo.isp ?? geo.org,
      avgRtt: hop.avg_rtt,
      isOrigin: false,
      hopLabel: `Hop ${hop.hop}`,
      arrivedAt: 0,
    };
    this.allPointsForFraming.push([node.lon, node.lat]);

    if (this.pendingTimeouts.length > 0) {
      this.queue.push({ kind: "ghost", to: node, hops: [...this.pendingTimeouts] });
      this.pendingTimeouts = [];
    }
    this.queue.push({ kind: "move", to: node, hop });
    this.ensureTicking();
    this.kick();
  }

  /** Call once the SSE `done` event arrives. Trailing unresolved timeouts
   * (no further known point reached) simply have no destination to draw
   * toward and are left out of the globe -- they're still in the hop list. */
  finish() {
    this.finished = true;
    this.kick();
  }

  private kick() {
    if (this.playing) return;
    this.playing = true;
    void this.playLoop();
  }

  private async playLoop() {
    while (this.queue.length > 0) {
      const beat = this.queue.shift()!;
      await this.playBeat(beat);
    }
    this.playing = false;
    if (this.finished) {
      this.globe.flyToFrameAll(this.allPointsForFraming);
      this.onFinished?.();
    }
  }

  private playBeat(beat: Beat): Promise<void> {
    const from = this.lastKnownNode;
    const to = beat.to;

    if (!from) {
      // No usable starting point (origin never resolved) -- settle instantly.
      this.settleNode(to);
      return Promise.resolve();
    }

    const fromLL: LonLat = [from.lon, from.lat];
    const toLL: LonLat = [to.lon, to.lat];
    const path = greatCirclePath(fromLL, toLL, 128);
    const distanceKm = haversineKm(fromLL, toLL);

    let color: RGB;
    let duration: number;
    if (beat.kind === "move") {
      const rtt = beat.hop.avg_rtt;
      const delta = rtt != null && this.lastRtt != null ? Math.max(0, rtt - this.lastRtt) : (rtt ?? 60);
      color = colorForDelta(delta);
      duration = durationForDelta(delta);
      if (rtt != null) this.lastRtt = rtt;
    } else {
      color = GHOST_COLOR;
      duration = Math.min(GHOST_BASE_DURATION + beat.hops.length * 180, 2000);
    }

    const midIdx = Math.floor(path.length / 2);
    this.globe.easeToFrame(path[midIdx], {
      zoom: zoomForDistanceKm(distanceKm),
      pitch: 40,
      duration: duration + ARRIVAL_PAUSE,
    });

    this.activeBeat = { beat, path, color, startedAt: performance.now(), duration };

    return new Promise((resolve) => {
      const check = () => {
        if (!this.activeBeat) {
          resolve();
          return;
        }
        const elapsed = performance.now() - this.activeBeat.startedAt;
        if (elapsed >= this.activeBeat.duration) {
          this.finalizeActiveBeat();
          setTimeout(resolve, ARRIVAL_PAUSE);
        } else {
          requestAnimationFrame(check);
        }
      };
      requestAnimationFrame(check);
    });
  }

  private finalizeActiveBeat() {
    if (!this.activeBeat) return;
    const { beat, path, color } = this.activeBeat;

    if (beat.kind === "ghost") {
      const midpoint = path[Math.floor(path.length / 2)];
      this.settledArcs.push({ id: `ghost-${beat.to.id}`, path, color, dashed: true, ghostMidpoint: midpoint });
    } else {
      this.settledArcs.push({ id: `arc-${beat.to.id}`, path, color, dashed: false });
    }

    this.activeBeat = null;
    this.settleNode(beat.to);
  }

  private settleNode(node: AnimNode) {
    node.arrivedAt = performance.now();
    this.settledNodes.push(node);
    this.lastKnownNode = node;
    this.onArrive?.(node);
  }

  private ensureTicking() {
    if (this.tickHandle !== null) return;
    const tick = () => {
      this.render();
      this.tickHandle = requestAnimationFrame(tick);
    };
    this.tickHandle = requestAnimationFrame(tick);
  }

  private render() {
    const now = performance.now();
    const layers: Layer[] = [];

    const solidArcs = this.settledArcs.filter((a) => !a.dashed);
    const ghostArcs = this.settledArcs.filter((a) => a.dashed);

    if (solidArcs.length > 0) {
      layers.push(
        new PathLayer<SettledArc>({
          id: "settled-arcs",
          data: solidArcs,
          getPath: (d) => d.path,
          getColor: (d) => [d.color[0], d.color[1], d.color[2], 200],
          getWidth: 2,
          widthUnits: "pixels",
          pickable: false,
        })
      );
    }

    if (ghostArcs.length > 0) {
      const dashData: { path: LonLat[] }[] = [];
      for (const g of ghostArcs) {
        for (const seg of dashSegments(g.path)) dashData.push({ path: seg });
      }
      layers.push(
        new PathLayer<{ path: LonLat[] }>({
          id: "ghost-arcs",
          data: dashData,
          getPath: (d) => d.path,
          getColor: [150, 158, 172, 150],
          getWidth: 1.5,
          widthUnits: "pixels",
          pickable: false,
        })
      );

      const pulse = 0.5 + 0.5 * Math.sin(now / 420);
      layers.push(
        new TextLayer<SettledArc>({
          id: "ghost-markers",
          data: ghostArcs,
          getPosition: (d) => d.ghostMidpoint as LonLat,
          getText: () => "?",
          getSize: 14 + pulse * 4,
          getColor: [170, 178, 190, Math.round(140 + pulse * 100)],
          fontFamily: "monospace",
          fontWeight: 700,
        })
      );
    }

    const nodeData = this.settledNodes.map((n) => {
      const age = now - n.arrivedAt;
      const scale = age < POP_DURATION ? 0.3 + 0.7 * easeOutBack(age / POP_DURATION) : 1;
      return { node: n, scale };
    });

    layers.push(
      new ScatterplotLayer<{ node: AnimNode; scale: number }>({
        id: "settled-nodes",
        data: nodeData,
        getPosition: (d) => [d.node.lon, d.node.lat],
        getRadius: (d) => (d.node.isOrigin ? 65000 : 42000) * Math.max(d.scale, 0),
        getFillColor: (d) => (d.node.isOrigin ? [76, 224, 255, 235] : [255, 200, 120, 225]),
        getLineColor: [255, 255, 255, 180],
        lineWidthMinPixels: 1,
        stroked: true,
        radiusUnits: "meters",
        pickable: false,
        updateTriggers: { getRadius: now },
      })
    );

    const ripples = this.settledNodes
      .map((n) => ({ node: n, age: now - n.arrivedAt }))
      .filter((r) => r.age >= 0 && r.age < RIPPLE_DURATION);

    if (ripples.length > 0) {
      layers.push(
        new ScatterplotLayer<{ node: AnimNode; age: number }>({
          id: "ripples",
          data: ripples,
          getPosition: (d) => [d.node.lon, d.node.lat],
          getRadius: (d) => (d.age / RIPPLE_DURATION) * 260000,
          getFillColor: [0, 0, 0, 0],
          getLineColor: (d) => {
            const alpha = Math.round(220 * (1 - d.age / RIPPLE_DURATION));
            return d.node.isOrigin ? [76, 224, 255, alpha] : [255, 200, 120, alpha];
          },
          lineWidthMinPixels: 2,
          stroked: true,
          filled: false,
          radiusUnits: "meters",
          pickable: false,
          updateTriggers: { getRadius: now, getLineColor: now },
        })
      );
    }

    if (this.activeBeat) {
      const { path, color, startedAt, duration } = this.activeBeat;
      const rawT = Math.min((now - startedAt) / duration, 1);
      const t = easeInOutQuad(rawT);
      const headIdx = Math.min(Math.floor(t * (path.length - 1)), path.length - 1);
      const trailStart = Math.max(0, headIdx - TRAIL_LEN);

      const trailData: { path: LonLat[]; alpha: number; w: number }[] = [];
      for (let i = trailStart; i < headIdx; i++) {
        const frac = (i - trailStart) / Math.max(headIdx - trailStart, 1);
        trailData.push({ path: [path[i], path[i + 1]], alpha: Math.round(20 + frac * 210), w: 1 + frac * 3 });
      }

      if (trailData.length > 0) {
        layers.push(
          new PathLayer<{ path: LonLat[]; alpha: number; w: number }>({
            id: "packet-trail",
            data: trailData,
            getPath: (d) => d.path,
            getColor: (d) => [color[0], color[1], color[2], d.alpha],
            getWidth: (d) => d.w,
            widthUnits: "pixels",
            pickable: false,
          })
        );
      }

      const headPos = path[headIdx];
      layers.push(
        new ScatterplotLayer<LonLat>({
          id: "packet-halo",
          data: [headPos],
          getPosition: (d) => d,
          getRadius: 55000,
          getFillColor: [color[0], color[1], color[2], 90],
          radiusUnits: "meters",
          pickable: false,
        }),
        new ScatterplotLayer<LonLat>({
          id: "packet-head",
          data: [headPos],
          getPosition: (d) => d,
          getRadius: 22000,
          getFillColor: [255, 255, 255, 240],
          radiusUnits: "meters",
          pickable: false,
        })
      );
    }

    this.globe.setOverlayLayers(layers);
  }
}
