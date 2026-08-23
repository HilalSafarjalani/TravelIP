import "./style.css";
import { Globe } from "./globe";
import { AnimationEngine, type AnimNode } from "./animator";
import { startTrace } from "./api";
import { flagEmoji } from "./geoutil";
import type { DoneInfo, ErrorInfo, GeoData, HopData, HopRecord, OriginData } from "./types";

const mapContainer = document.getElementById("map") as HTMLElement;
const targetInput = document.getElementById("target-input") as HTMLInputElement;
const modeSelect = document.getElementById("mode-select") as HTMLSelectElement;
const goBtn = document.getElementById("go-btn") as HTMLButtonElement;
const hopPanel = document.getElementById("hop-panel") as HTMLElement;
const hopList = document.getElementById("hop-list") as HTMLElement;
const statusBar = document.getElementById("status-bar") as HTMLElement;
const labelCard = document.getElementById("hop-label-card") as HTMLElement;

const globe = new Globe(mapContainer);
const engine = new AnimationEngine(globe);

const hops = new Map<number, HopRecord>();
let currentSource: EventSource | null = null;
let labelFollowNode: AnimNode | null = null;

function setStatus(message: string, isError = false) {
  statusBar.textContent = message;
  statusBar.classList.toggle("visible", Boolean(message));
  statusBar.classList.toggle("error", isError);
}

function fmtRtt(hop: HopRecord): string {
  if (hop.timeout) return "timeout";
  if (hop.avg_rtt == null) return "-";
  return `${hop.avg_rtt.toFixed(1)} ms`;
}

function fmtGeo(hop: HopRecord): string {
  if (hop.timeout) return "request timed out — not mappable";
  const g = hop.geo;
  if (!g) return "resolving location…";
  if (g.kind === "private") return "local network — not mappable";
  if (g.kind === "cgnat") return "carrier-grade NAT — not mappable";
  if (g.kind === "unknown") return "location unknown — not mappable";
  const bits = [g.city, g.country].filter(Boolean);
  const loc = bits.length ? bits.join(", ") : "location unknown";
  const asn = g.asn ? ` · ${g.asn}` : "";
  return `${loc}${asn}`;
}

function renderHopList() {
  hopPanel.classList.add("visible");
  const sorted = [...hops.values()].sort((a, b) => a.hop - b.hop);
  hopList.innerHTML = "";
  for (const hop of sorted) {
    const mappable = hop.geo?.kind === "public";
    const row = document.createElement("div");
    row.className = `hop-row ${mappable ? "mappable" : "not-mappable"}`;
    row.innerHTML = `
      <div class="hop-row-top">
        <span><span class="hop-num">${hop.hop}</span> <span class="hop-ip">${hop.ip ?? "* * *"}</span></span>
        <span class="hop-rtt">${fmtRtt(hop)}</span>
      </div>
      <div class="hop-geo">${fmtGeo(hop)}</div>
    `;
    hopList.appendChild(row);
  }
}

function showLabelCard(node: AnimNode) {
  const flag = flagEmoji(node.countryCode);
  const loc = [node.city, node.country].filter(Boolean).join(", ") || "Unknown location";
  const ispBits = [node.isp, node.asn].filter(Boolean).join(" · ");
  const rttRow = node.isOrigin
    ? ""
    : `<div class="label-rtt">${node.avgRtt != null ? `${node.avgRtt.toFixed(1)} ms avg` : "—"}</div>`;

  labelCard.innerHTML = `
    <div class="label-flag">${flag}</div>
    <div class="label-body">
      <div class="label-title">${node.hopLabel}</div>
      <div class="label-loc">${loc}</div>
      ${ispBits ? `<div class="label-meta">${ispBits}</div>` : ""}
      ${rttRow}
    </div>
  `;
  labelCard.classList.add("visible");
  labelFollowNode = node;
}

function labelFollowTick() {
  if (labelFollowNode) {
    const p = globe.map.project([labelFollowNode.lon, labelFollowNode.lat]);
    labelCard.style.transform = `translate(${p.x}px, ${p.y}px) translate(-50%, -130%)`;
  }
  requestAnimationFrame(labelFollowTick);
}
requestAnimationFrame(labelFollowTick);

engine.onArrive = (node) => showLabelCard(node);

function startNewTrace() {
  const target = targetInput.value.trim();
  if (!target) return;

  if (currentSource) {
    currentSource.close();
    currentSource = null;
  }

  hops.clear();
  hopList.innerHTML = "";
  labelCard.classList.remove("visible");
  labelFollowNode = null;
  engine.reset();
  setStatus("Starting trace…");
  goBtn.disabled = true;
  globe.stopAutoRotate();

  const mode = modeSelect.value === "tcp" ? "tcp" : "icmp";

  currentSource = startTrace(target, mode, {
    onOrigin: (o: OriginData) => {
      engine.setOrigin(o);
    },
    onHop: (h: HopData) => {
      const record: HopRecord = { ...h };
      hops.set(h.hop, record);
      renderHopList();
      setStatus(`Hop ${h.hop}…`);
      if (h.timeout) {
        // No geo event will ever arrive for a timed-out hop -- resolve now.
        engine.submitHop(record);
      }
    },
    onGeo: (g: GeoData) => {
      const existing = hops.get(g.hop);
      if (existing) {
        existing.geo = g;
        renderHopList();
        engine.submitHop(existing);
      }
    },
    onDone: (info: DoneInfo) => {
      setStatus(`Trace complete — ${info.hop_count} hops.`);
      goBtn.disabled = false;
      engine.finish();
    },
    onError: (info: ErrorInfo) => {
      setStatus(info.message, true);
      goBtn.disabled = false;
    },
  });
}

goBtn.addEventListener("click", startNewTrace);
targetInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") startNewTrace();
});
