import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { MapboxOverlay } from "@deck.gl/mapbox";
import type { Layer } from "@deck.gl/core";
import type { LonLat } from "./geoutil";

const CARTO_DARK_STYLE = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";

/**
 * Thin wrapper around the MapLibre globe + deck.gl overlay plumbing. Layer
 * composition (nodes, arcs, the animated packet, ripples) lives in
 * AnimationEngine -- this class only owns the map, camera helpers, and
 * pushing whatever layer array it's handed to the overlay.
 */
export class Globe {
  map: maplibregl.Map;
  private overlay: MapboxOverlay;
  private autoRotate = false;
  private rotateHandle: number | null = null;
  private loaded = false;
  private onLoadCallbacks: (() => void)[] = [];

  constructor(container: HTMLElement) {
    this.map = new maplibregl.Map({
      container,
      style: CARTO_DARK_STYLE,
      center: [10, 20],
      zoom: 1.3,
      pitch: 0,
      attributionControl: false,
    });

    this.overlay = new MapboxOverlay({ interleaved: true, layers: [] });

    this.map.on("load", () => {
      this.map.setProjection({ type: "globe" });
      this.map.addControl(this.overlay as unknown as maplibregl.IControl);
      this.addAtmosphere();
      this.startAutoRotate();
      this.startRepaintLoop();
      this.loaded = true;
      for (const cb of this.onLoadCallbacks) cb();
      this.onLoadCallbacks = [];
    });

    for (const evt of ["mousedown", "wheel", "touchstart", "dragstart"] as const) {
      this.map.on(evt, () => this.stopAutoRotate());
    }
  }

  whenLoaded(cb: () => void) {
    if (this.loaded) cb();
    else this.onLoadCallbacks.push(cb);
  }

  private addAtmosphere() {
    this.map.setSky({
      "sky-color": "#05070d",
      "horizon-color": "#142a4a",
      "fog-color": "#05070d",
      "atmosphere-blend": 0.6,
    });
  }

  startAutoRotate() {
    this.autoRotate = true;
    const step = () => {
      if (!this.autoRotate) return;
      const center = this.map.getCenter();
      center.lng -= 0.03;
      this.map.setCenter(center);
      this.rotateHandle = requestAnimationFrame(step);
    };
    this.rotateHandle = requestAnimationFrame(step);
  }

  stopAutoRotate() {
    this.autoRotate = false;
    if (this.rotateHandle !== null) {
      cancelAnimationFrame(this.rotateHandle);
      this.rotateHandle = null;
    }
  }

  /**
   * MapLibre pauses its render loop once nothing changes camera-side. The
   * interleaved deck.gl overlay needs the base map to keep repainting every
   * frame it draws, or the shared WebGL context can freeze on a bad frame
   * (observed as the whole basemap going solid black) the moment auto-rotate
   * -- which was incidentally forcing repaints via per-frame setCenter --
   * stops. Keep an explicit, cheap repaint ticking for the map's lifetime.
   */
  private startRepaintLoop() {
    const loop = () => {
      this.map.triggerRepaint();
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  }

  setOverlayLayers(layers: Layer[]) {
    if (!this.loaded) return;
    this.overlay.setProps({ layers });
  }

  /** Smoothly reframes the camera on a point mid-journey (called per hop). */
  easeToFrame(center: LonLat, opts: { zoom: number; pitch?: number; bearing?: number; duration: number }) {
    this.map.easeTo({
      center,
      zoom: opts.zoom,
      pitch: opts.pitch ?? 40,
      bearing: opts.bearing ?? this.map.getBearing(),
      duration: opts.duration,
      essential: true,
    });
  }

  /**
   * Final "frame the whole path" shot. Uses a flat lon/lat bounding box,
   * which is only an approximation on a sphere -- a path spanning more than
   * one hemisphere can't be framed from a single camera angle, so very wide
   * paths (e.g. crossing an ocean and a continent) may still clip one end.
   * Good enough as the resting shot; per-hop framing during the journey
   * (easeToFrame above) is what actually shows every location up close.
   */
  flyToFrameAll(points: LonLat[]) {
    if (points.length === 0) return;
    if (points.length === 1) {
      this.map.flyTo({ center: points[0], zoom: 3, pitch: 0, duration: 1600 });
      return;
    }
    const lons = points.map((p) => p[0]);
    const lats = points.map((p) => p[1]);
    const bounds: [LonLat, LonLat] = [
      [Math.min(...lons), Math.min(...lats)],
      [Math.max(...lons), Math.max(...lats)],
    ];
    this.map.fitBounds(bounds, { padding: 100, duration: 1800, maxZoom: 4.5, pitch: 0 });
  }
}
