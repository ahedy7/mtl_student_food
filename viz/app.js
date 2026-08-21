/* ─────────────────────────────────────────────────────────────────────────
   Mtl Food  –  client-side app
   Architecture: load binary graph → build CSR + grid hash → Dijkstra on pin drop → score
   ───────────────────────────────────────────────────────────────────────── */

const INITIAL_VIEW = { longitude: -73.5752, latitude: 45.5088, zoom: 13, pitch: 0, bearing: 0 };
const PRICE_LABELS = { 0: "?", 1: "$", 2: "$$", 3: "$$$", 4: "$$$$" };

const BASEMAPS = {
  "dark-matter":      "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
  "positron":         "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
  "voyager":          "https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json",
  "ofm-liberty":      "https://tiles.openfreemap.org/styles/liberty",
  "ofm-positron":     "https://tiles.openfreemap.org/styles/positron",
  "stadia-smooth-dark": "https://tiles.stadiamaps.com/styles/alidade_smooth_dark.json",
  "stadia-smooth":    "https://tiles.stadiamaps.com/styles/alidade_smooth.json",
  "stadia-toner":     "https://tiles.stadiamaps.com/styles/stamen_toner.json",
  "stadia-toner-lite":"https://tiles.stadiamaps.com/styles/stamen_toner_lite.json",
};

/* ── Speed constants — must match prep/snap.py WALK/BIKE_SPEED_MPS ───────── */
const WALK_MPS = 4800  / 3600;   // 4.8 km/h
const BIKE_MPS = 15000 / 3600;   // 15.0 km/h

/* Dev mode: on localhost, or any URL with ?debug=1. Enables the straight-line
   reachability assertion below, which is O(results) per render and only useful
   while developing. */
const DEV = location.hostname === "localhost"
         || location.hostname === "127.0.0.1"
         || new URLSearchParams(location.search).has("debug");

/* Multiplier on mode speed for the straight-line bound — mirrors
   BOUND_SPEED_FACTOR in prep/snap.py. Keep the two in step. */
const BOUND_SPEED_FACTOR = 2.0;

/* ── Heating-up thresholds (tweak as needed) ────────────────────────────── */
const HEAT_MIN_SHARE    = 0.5;   // >= half the review sample from last 6 months
const HEAT_MAX_REVIEWS  = 300;   // still gaining traction (not yet mobbed)
const HEAT_MIN_REVIEWS  = 15;    // need enough sample to trust the ratio
const HEAT_MIN_VELOCITY = 1.5;   // reviews/week needed to qualify via velocity

/* ── State ───────────────────────────────────────────────────────────────── */
const state = {
  mode:     "walk",
  cutoff:   20,        // minutes
  prices:   new Set(),   // empty = show all prices
  w:        0.6,       // quality weight (0 = all closeness, 1 = all quality)
  view:     "best",    // "best" | "gems"
  category: null,      // null = all, or a Google type key string
  openNow:  false,     // filter to currently-open places only
  pin:      null,      // { lat, lon }
  hover:    null,
  selected: null,
  search:   "",
};

/* ── Weight label (top-level so syncUIFromState can call it) ─────────────── */
function updateWeightLabel() {
  const pct = Math.round(state.w * 100);
  document.getElementById("weight-display").textContent = pct === 50 ? "balanced"
    : pct > 50 ? `${pct}% rating`
    : `${100 - pct}% proximity`;
}

/* ── URL state ───────────────────────────────────────────────────────────── */
function serializeState() {
  const p = new URLSearchParams();
  if (state.pin) {
    p.set("lat", state.pin.lat.toFixed(5));
    p.set("lon", state.pin.lon.toFixed(5));
  }
  if (state.mode !== "walk")    p.set("mode", state.mode);
  if (state.cutoff !== 20)      p.set("cutoff", state.cutoff);
  if (state.prices.size > 0)    p.set("prices", [...state.prices].sort().join(","));
  if (state.w !== 0.6)          p.set("w", state.w);
  if (state.view !== "best")    p.set("view", state.view);
  if (state.category)           p.set("cat", state.category);
  if (state.openNow)            p.set("open", "1");
  if (state.search)             p.set("q", state.search);
  return p;
}

function parseStateFromURL() {
  const p = new URLSearchParams(location.search);
  const lat = parseFloat(p.get("lat")), lon = parseFloat(p.get("lon"));
  if (isFinite(lat) && isFinite(lon) && lat >= 45 && lat <= 46 && lon >= -74 && lon <= -73)
    state.pin = { lat, lon };
  const mode = p.get("mode");
  if (mode === "walk" || mode === "bike") state.mode = mode;
  const cutoff = parseInt(p.get("cutoff"), 10);
  if (isFinite(cutoff) && cutoff >= 5 && cutoff <= 60) state.cutoff = cutoff;
  const pricesStr = p.get("prices");
  if (pricesStr) state.prices = new Set(pricesStr.split(",").map(Number).filter(n => [0,1,2,3,4].includes(n)));
  const w = parseFloat(p.get("w"));
  if (isFinite(w) && w >= 0 && w <= 1) state.w = w;
  const view = p.get("view");
  if (view === "best" || view === "gems") state.view = view;
  const cat = p.get("cat");
  if (cat) state.category = cat;
  if (p.get("open") === "1") state.openNow = true;
  const q = p.get("q");
  if (q) state.search = q;
}

let _urlTimer = null;
function pushStateDebounced() {
  clearTimeout(_urlTimer);
  _urlTimer = setTimeout(() => {
    const qs = serializeState().toString();
    history.replaceState(null, "", qs ? `?${qs}` : location.pathname);
  }, 250);
}

function syncUIFromState() {
  document.querySelectorAll(".toggle-btn").forEach(b =>
    b.classList.toggle("active", b.dataset.mode === state.mode));
  document.getElementById("slider-cutoff").value = state.cutoff;
  document.getElementById("cutoff-display").textContent = `${state.cutoff} min`;
  document.getElementById("slider-weight").value = state.w;
  updateWeightLabel();
  document.querySelectorAll(".price-btn").forEach(b =>
    b.classList.toggle("active", state.prices.has(+b.dataset.price)));
  document.querySelectorAll(".category-row .cat-btn").forEach(b =>
    b.classList.toggle("active", (b.dataset.cat || null) === state.category));
  document.getElementById("tab-best").classList.toggle("active", state.view === "best");
  document.getElementById("tab-gems").classList.toggle("active", state.view === "gems");
  document.getElementById("btn-open-now").classList.toggle("active", state.openNow);
  const sb = document.getElementById("search-bar");
  if (sb) sb.value = state.search;
}

/* ── Data ────────────────────────────────────────────────────────────────── */
let places        = [];
let meanRating    = 0;
let graphs        = { walk: null, bike: null };
let adjLists      = { walk: null, bike: null };
let gridLists     = { walk: null, bike: null };
let reviewHistory = {};   // place_id → [{date, count}, ...]
let lastScored    = [];
let lastDistArr   = null;
let lastGraph     = null;

/* ── deck.gl + MapLibre ──────────────────────────────────────────────────── */
let deckgl = null;
let maplib = null;

/* ══════════════════════════════════════════════════════════════════════════
   GRAPH UTILITIES
   ══════════════════════════════════════════════════════════════════════════ */

class MinHeap {
  constructor() { this._h = []; }
  get size()     { return this._h.length; }
  push(item)     { this._h.push(item); this._up(this._h.length - 1); }
  pop() {
    const top = this._h[0];
    const last = this._h.pop();
    if (this._h.length) { this._h[0] = last; this._down(0); }
    return top;
  }
  _up(i) {
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (this._h[p][0] <= this._h[i][0]) break;
      [this._h[p], this._h[i]] = [this._h[i], this._h[p]];
      i = p;
    }
  }
  _down(i) {
    const n = this._h.length;
    for (;;) {
      let m = i, l = 2*i+1, r = 2*i+2;
      if (l < n && this._h[l][0] < this._h[m][0]) m = l;
      if (r < n && this._h[r][0] < this._h[m][0]) m = r;
      if (m === i) break;
      [this._h[m], this._h[i]] = [this._h[i], this._h[m]];
      i = m;
    }
  }
}

function buildCSR(edgeFlat, timesFlat, nodeCount) {
  const edgeCount = timesFlat.length;
  const degree = new Uint32Array(nodeCount);
  for (let i = 0; i < edgeCount; i++) {
    degree[edgeFlat[i * 2]]++;
    degree[edgeFlat[i * 2 + 1]]++;
  }
  const offsets = new Uint32Array(nodeCount + 1);
  for (let i = 0; i < nodeCount; i++) offsets[i + 1] = offsets[i] + degree[i];
  const nbrs = new Uint32Array(offsets[nodeCount]);
  const wts  = new Uint32Array(offsets[nodeCount]);
  const pos  = new Uint32Array(nodeCount);
  for (let i = 0; i < edgeCount; i++) {
    const u = edgeFlat[i * 2], v = edgeFlat[i * 2 + 1], t = timesFlat[i];
    const pu = offsets[u] + pos[u]++;
    nbrs[pu] = v; wts[pu] = t;
    const pv = offsets[v] + pos[v]++;
    nbrs[pv] = u; wts[pv] = t;
  }
  return { offsets, nbrs, wts };
}

const GRID_DEG = 0.002;  // ~200 m buckets at Montreal's latitude

function buildNodeGrid(nodeFlat) {
  const grid = new Map();
  const n = nodeFlat.length >> 1;
  for (let i = 0; i < n; i++) {
    const key = `${Math.floor(nodeFlat[i*2] / GRID_DEG)},${Math.floor(nodeFlat[i*2+1] / GRID_DEG)}`;
    let cell = grid.get(key);
    if (!cell) { cell = []; grid.set(key, cell); }
    cell.push(i);
  }
  return grid;
}

/* Snap a pin to the nearest graph node, searching outward a ring at a time.

   Returns -1 when there is no node within MAX_PIN_SNAP_RINGS — a pin in the
   river, outside the bbox, or on a landmass the graph does not cover (the walk
   graph does not include Ile-des-Soeurs). It must NOT fall back to node 0: that
   silently routes from an arbitrary node and produces plausible, entirely false
   travel times, which is the same failure that shipped once via stale place node
   ids. Callers must handle -1. */
const MAX_PIN_SNAP_RINGS = 3;
/* Same 500 m ceiling prep/snap.py applies to places. Beyond it the "nearest" node
   is somewhere you cannot actually walk to — across the river, say — and routing
   from it would report times for a journey the user cannot make. */
const MAX_PIN_SNAP_M = 500;

function nearestNodeGrid(grid, nodeFlat, lat, lon) {
  const br = Math.floor(lat / GRID_DEG), bc = Math.floor(lon / GRID_DEG);
  let bestD = Infinity, bestI = -1;

  for (let ring = 0; ring <= MAX_PIN_SNAP_RINGS; ring++) {
    for (let dr = -ring; dr <= ring; dr++) {
      for (let dc = -ring; dc <= ring; dc++) {
        // Only the perimeter of this ring; inner cells were done already.
        if (ring > 0 && Math.abs(dr) !== ring && Math.abs(dc) !== ring) continue;
        const cell = grid.get(`${br+dr},${bc+dc}`);
        if (!cell) continue;
        for (const i of cell) {
          const dlat = nodeFlat[i*2]-lat, dlon = nodeFlat[i*2+1]-lon;
          const d = dlat*dlat + dlon*dlon;
          if (d < bestD) { bestD = d; bestI = i; }
        }
      }
    }
    // Nothing in an outer ring can beat a hit already inside this one.
    if (bestI >= 0 && bestD <= (ring * GRID_DEG) ** 2) break;
  }
  if (bestI < 0) return -1;
  const snapM = haversineM(lat, lon, nodeFlat[bestI*2], nodeFlat[bestI*2+1]);
  return snapM <= MAX_PIN_SNAP_M ? bestI : -1;
}

function haversineM(lat1, lon1, lat2, lon2) {
  const R  = 6_371_000;
  const φ1 = lat1 * Math.PI / 180, φ2 = lat2 * Math.PI / 180;
  const dφ = (lat2 - lat1) * Math.PI / 180;
  const dλ = (lon2 - lon1) * Math.PI / 180;
  const a  = Math.sin(dφ/2) ** 2 + Math.cos(φ1) * Math.cos(φ2) * Math.sin(dλ/2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

/* Dijkstra single-source, capped at cutoffSec.
   Uses CSR adjacency. Returns Float64Array of travel times (Infinity = unreachable/over cap). */
function dijkstra(csr, nodeCount, start, cutoffSec) {
  const dist = new Float64Array(nodeCount).fill(Infinity);
  dist[start] = 0;
  const heap = new MinHeap();
  heap.push([0, start]);
  while (heap.size > 0) {
    const [d, u] = heap.pop();
    if (d > dist[u]) continue;
    if (d > cutoffSec) break;
    const end = csr.offsets[u + 1];
    for (let i = csr.offsets[u]; i < end; i++) {
      const nd = d + csr.wts[i];
      if (nd < dist[csr.nbrs[i]]) {
        dist[csr.nbrs[i]] = nd;
        heap.push([nd, csr.nbrs[i]]);
      }
    }
  }
  return dist;
}

/* Andrew's monotone chain — returns convex hull of [[x,y],...] in CCW order. */
function convexHull(pts) {
  if (pts.length < 3) return pts;
  const s = [...pts].sort((a, b) => a[0] !== b[0] ? a[0] - b[0] : a[1] - b[1]);
  const cross = (o, a, b) => (a[0]-o[0])*(b[1]-o[1]) - (a[1]-o[1])*(b[0]-o[0]);
  const lower = [];
  for (const p of s) {
    while (lower.length >= 2 && cross(lower[lower.length-2], lower[lower.length-1], p) <= 0) lower.pop();
    lower.push(p);
  }
  const upper = [];
  for (let i = s.length - 1; i >= 0; i--) {
    const p = s[i];
    while (upper.length >= 2 && cross(upper[upper.length-2], upper[upper.length-1], p) <= 0) upper.pop();
    upper.push(p);
  }
  lower.pop();
  upper.pop();
  return lower.concat(upper);
}

/* Returns [lon,lat] convex hull of all reachable graph nodes, or null. */
function isochronePolygon() {
  if (!lastGraph || !lastDistArr) return null;
  const nodes = lastGraph.nodeFlat;
  const n = nodes.length >> 1;
  const cutoffSec = state.cutoff * 60;
  const pts = [];
  for (let i = 0; i < n; i++) {
    if (lastDistArr[i] <= cutoffSec) {
      pts.push([nodes[i*2+1], nodes[i*2]]); // [lon, lat] for deck.gl
    }
  }
  return pts.length >= 3 ? convexHull(pts) : null;
}

/* ══════════════════════════════════════════════════════════════════════════
   SCORING
   ══════════════════════════════════════════════════════════════════════════ */

function filterAndScore(distArr, cutoffSec, pinSnapSec) {
  const cutoffMin = cutoffSec / 60;
  const m = 50;    // Bayesian prior weight
  const C = meanRating;
  const nodeKey  = state.mode === "walk" ? "walk_node"   : "bike_node";
  const snapKey  = state.mode === "walk" ? "walk_snap_m" : "bike_snap_m";
  const speedMps = state.mode === "walk" ? WALK_MPS      : BIKE_MPS;

  // Hard filters: reachability + price + category; compute total time including both snap legs
  const nodeCount = distArr.length;
  let survivors = places.flatMap(p => {
    // A null node means the place is not on this network (e.g. inside a park the
    // bike graph does not enter). A non-integer or out-of-range id means the data
    // is corrupt. Either way the place is unreachable — never fall back to node 0,
    // which would report a travel time measured from someone else's doorstep.
    const nodeId = p[nodeKey];
    if (!Number.isInteger(nodeId) || nodeId < 0 || nodeId >= nodeCount) return [];
    const graphSec = distArr[nodeId];
    if (!isFinite(graphSec)) return [];
    const placeSnapSec = (p[snapKey] ?? 0) / speedMps;
    const totalSec = graphSec + pinSnapSec + placeSnapSec;
    if (totalSec > cutoffSec) return [];
    if (state.prices.size > 0 && !state.prices.has(p.price ?? 0)) return [];
    if (state.category && !(p.types || []).includes(state.category)) return [];
    if (state.openNow && isOpenNow(p.hours) === false) return [];
    return [{ ...p, travelMin: totalSec / 60, distM: totalSec * speedMps }];
  });

  if (!survivors.length) return [];

  // Shrunk Bayesian rating
  survivors.forEach(p => {
    const v = p.reviews || 0;
    p._shrunkR = (v / (v + m)) * p.rating + (m / (v + m)) * C;
  });

  // Min-max scale shrunk rating across survivors
  let minR = Infinity, maxR = -Infinity;
  for (const p of survivors) {
    if (p._shrunkR < minR) minR = p._shrunkR;
    if (p._shrunkR > maxR) maxR = p._shrunkR;
  }
  const rangeR = maxR - minR || 1;

  const w = state.w;
  survivors.forEach(p => {
    p.ratingScaled = (p._shrunkR - minR) / rangeR;
    p.closeness    = Math.max(0, 1 - p.travelMin / cutoffMin);
    p.score        = w * p.ratingScaled + (1 - w) * p.closeness;
  });

  if (DEV) assertCrowBound(survivors, cutoffSec, speedMps);

  return survivors.sort((a, b) => b.score - a.score);
}

/* Straight-line reachability assertion (dev mode only).

   A real route is never shorter than the straight line, so nothing reachable in
   `cutoffSec` can be farther from the pin than cutoffSec x speed. This holds no
   matter how the graph is stored, so unlike the node-id range check it survives
   a change to the binary format, the CSR build, or Dijkstra itself.

   Mirrors check_crow_bound() in prep/snap.py; `python prep/reannotate_places.py
   --check` runs the same assertion over sample pins in CI. */
function assertCrowBound(survivors, cutoffSec, speedMps) {
  if (!state.pin) return;
  const limitM = speedMps * BOUND_SPEED_FACTOR * cutoffSec;

  const bad = [];
  for (const p of survivors) {
    const crow = haversineM(state.pin.lat, state.pin.lon, p.lat, p.lon);
    if (crow > limitM) {
      bad.push({
        name: p.name,
        crow_m: Math.round(crow),
        limit_m: Math.round(limitM),
        reported_min: +p.travelMin.toFixed(1),
        implied_kmh: Math.round((crow / (p.travelMin * 60)) * 3.6),
      });
    }
  }
  if (!bad.length) return;

  bad.sort((a, b) => b.crow_m - a.crow_m);
  console.error(
    `[mtl-food] ${bad.length} result(s) violate the straight-line bound ` +
    `(${state.mode}, ${cutoffSec / 60} min, limit ${Math.round(limitM)} m). ` +
    `Reported travel times are not physically possible:`,
    bad.slice(0, 10)
  );
  return bad;
}

function hiddenGems(survivors) {
  const highRated = survivors.filter(p => p.rating >= 4.3);
  if (!highRated.length) return [];
  const counts = survivors.map(p => p.reviews).sort((a, b) => a - b);
  const q1 = counts[Math.floor(counts.length * 0.25)];
  return highRated
    .filter(p => p.reviews <= q1)
    .sort((a, b) => b._shrunkR - a._shrunkR);
}

/* ══════════════════════════════════════════════════════════════════════════
   MAP RENDERING
   ══════════════════════════════════════════════════════════════════════════ */

function scoreToColor(score) {
  // Gradient: dim blue-gray → teal
  const t = Math.max(0, Math.min(1, score));
  return [
    Math.round(60 + t * (0 - 60)),
    Math.round(80 + t * (212 - 80)),
    Math.round(120 + t * (170 - 120)),
    Math.round(180 + t * 75),
  ];
}

function gemColor() { return [255, 209, 102, 230]; }

function renderLayers(scored) {
  const { ScatterplotLayer, PolygonLayer } = deck;

  // Isochrone: convex hull of reachable nodes, drawn under restaurant dots
  const hull = isochronePolygon();
  const isoLayer = new PolygonLayer({
    id: "isochrone",
    data: hull ? [hull] : [],
    getPolygon: d => d,
    getFillColor: [0, 185, 160, 28],
    getLineColor: [0, 185, 160, 75],
    getLineWidth: 1.5,
    lineWidthUnits: "pixels",
    stroked: true,
    filled: true,
    pickable: false,
  });

  const viewPlaces = state.view === "gems" ? hiddenGems(scored) : scored;

  const restaurants = new ScatterplotLayer({
    id: "restaurants",
    data: viewPlaces,
    getPosition: d => [d.lon, d.lat],
    getRadius: d => state.selected === d.id ? (5 + d.score * 9) * 1.25 : 5 + d.score * 9,
    getFillColor: d => state.selected === d.id ? [255, 200, 0, 255] : state.view === "gems" ? gemColor() : scoreToColor(d.score),
    getLineColor: d => state.hover === d.id ? [255, 255, 255, 200] : [255, 255, 255, 40],
    lineWidthMinPixels: 1,
    stroked: true,
    radiusUnits: "pixels",
    pickable: true,
    autoHighlight: false,
    updateTriggers: {
      getFillColor: [state.view, state.hover, state.selected],
      getLineColor: [state.hover],
      getRadius: [state.selected],
    },
  });

  const selectedRing = new ScatterplotLayer({ id: "selected-ring", data: [] });

  const pinData = state.pin ? [state.pin] : [];
  const pinOuter = new ScatterplotLayer({
    id: "pin-ring",
    data: pinData,
    getPosition: d => [d.lon, d.lat],
    getRadius: 14,
    getFillColor: [0, 0, 0, 0],
    getLineColor: [255, 255, 255, 180],
    lineWidthMinPixels: 2,
    stroked: true,
    radiusUnits: "pixels",
    pickable: false,
  });
  const pinInner = new ScatterplotLayer({
    id: "pin-dot",
    data: pinData,
    getPosition: d => [d.lon, d.lat],
    getRadius: 7,
    getFillColor: [255, 70, 100, 255],
    radiusUnits: "pixels",
    pickable: false,
  });

  deckgl.setProps({ layers: [isoLayer, restaurants, selectedRing, pinOuter, pinInner] });
}

/* ══════════════════════════════════════════════════════════════════════════
   RESULTS LIST
   ══════════════════════════════════════════════════════════════════════════ */

/* Returns {day, hhmm} for the current moment in Montreal.
   The IANA zone is America/Toronto — Montreal shares it, there is no
   America/Montreal zone in the tz database. */
function montrealNow() {
  const parts = new Intl.DateTimeFormat("en", {
    timeZone: "America/Toronto",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(new Date());
  const get = t => parts.find(p => p.type === t)?.value ?? "0";
  const DAYS = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];
  return {
    day:  DAYS.indexOf(get("weekday")),
    hhmm: (parseInt(get("hour"), 10) % 24) * 100 + parseInt(get("minute"), 10),
  };
}

/* Core check — testable with explicit day/hhmm. Returns true/false/null. */
function checkHours(hours, day, hhmm) {
  if (!hours || !hours.length) return null;
  for (const [od, ot, cd, ct] of hours) {
    if (cd === -1) return true;                      // 24/7
    if (od === cd) {
      if (day === od && hhmm >= ot && hhmm < ct) return true;
    } else {
      // overnight: closes on the following calendar day
      if (day === od && hhmm >= ot) return true;
      if (day === cd && hhmm < ct)  return true;
    }
  }
  return false;
}

/* Returns true (open), false (closed), or null (hours unknown). */
function isOpenNow(hours) {
  if (!hours || !hours.length) return null;
  const { day, hhmm } = montrealNow();
  return checkHours(hours, day, hhmm);
}

function reviewVelocity(hist) {
  if (!hist || hist.length < 2) return null;
  const oldest = hist[0], latest = hist[hist.length - 1];
  const days = (new Date(latest.date) - new Date(oldest.date)) / 86_400_000;
  if (days < 1) return null;
  return (latest.count - oldest.count) / (days / 7);
}

// Returns null | 'velocity' | 'provisional'
function isHeatingUp(p) {
  if (p.reviews < HEAT_MIN_REVIEWS || p.reviews > HEAT_MAX_REVIEWS) return null;
  const hist = reviewHistory[p.id];
  if (hist && hist.length >= 3) {
    const v = reviewVelocity(hist);
    return v !== null && v >= HEAT_MIN_VELOCITY ? "velocity" : null;
  }
  return p.recent_share != null && p.recent_share >= HEAT_MIN_SHARE ? "provisional" : null;
}

function priceStr(price) { return PRICE_LABELS[price] ?? "?"; }
function modeIcon()      { return state.mode === "walk" ? "🚶" : "🚴"; }

const TYPE_LABELS = {
  cafe:                    "Café",
  bakery:                  "Bakery",
  bar:                     "Bar",
  meal_takeaway:           "Takeout",
  grocery_or_supermarket:  "Grocery",
  liquor_store:            "Liquor",
  night_club:              "Nightclub",
  convenience_store:       "Convenience",
  supermarket:             "Supermarket",
};
function typeLabel(types = []) {
  for (const t of types) {
    if (TYPE_LABELS[t]) return TYPE_LABELS[t];
  }
  return null;
}

function renderList(scored) {
  const list  = document.getElementById("results-list");
  const items = state.view === "gems" ? hiddenGems(scored) : scored;

  if (!items.length) {
    list.innerHTML = `<div class="results-empty">No restaurants found within ${state.cutoff} min. Try increasing the cutoff or moving the pin.</div>`;
    return;
  }

  const q = state.search.trim().toLowerCase();
  const filtered = q ? items.filter(p => {
    const tLabel = typeLabel(p.types) || "";
    return p.name.toLowerCase().includes(q) || tLabel.toLowerCase().includes(q);
  }) : items;

  if (!filtered.length) {
    list.innerHTML = `<div class="results-empty">No results match "${escHtml(state.search)}"</div>`;
    return;
  }

  const mapsUrl = id => `https://www.google.com/maps/place/?q=place_id:${id}`;

  list.innerHTML = filtered.slice(0, 60).map((p, i) => {
    const isTop   = i < 3 && state.view === "best" && !q;
    const isGem   = state.view === "gems";
    const pct     = Math.round(p.score * 100);
    const tMin    = p.travelMin.toFixed(0);
    const pLabel  = priceStr(p.price);
    const tLabel  = typeLabel(p.types);
    const rPct    = Math.round((p.ratingScaled ?? 0) * 100);
    const cPct    = Math.round((p.closeness ?? 0) * 100);
    const openStatus = isOpenNow(p.hours);
    const openBadge  = openStatus === true  ? `<span class="open-badge open">Open</span>`
                     : openStatus === false ? `<span class="open-badge closed">Closed</span>`
                     : `<span class="open-badge unknown">Hours unknown</span>`;
    // Verbatim, per the same policy as renderMetaAttributions above.
    const attribution = Array.isArray(p.attributions) && p.attributions.length
      ? `<div class="result-attribution">${p.attributions.join(" ")}</div>`
      : "";
    const heat = isHeatingUp(p);
    const heatBadge = heat ? `<span class="heat-badge">${heat === "provisional" ? "heating up (provisional)" : "heating up"}</span>` : "";

    return `<div class="result-item" data-id="${p.id}" tabindex="0">
      <div class="result-rank ${isTop ? "top" : ""}">${isGem ? "◆" : i + 1}</div>
      <div class="result-body">
        <div class="result-name-row">
          <span class="result-name">${escHtml(p.name)}</span>
          ${openBadge}
          ${heatBadge}
          <a class="maps-link" href="${mapsUrl(p.id)}" target="_blank" rel="noopener" title="Open in Google Maps" onclick="event.stopPropagation()">↗</a>
        </div>
        <div class="result-meta">
          <span class="result-summary">${tMin} min ${state.mode}, ${(p._shrunkR ?? p.rating).toFixed(1)} (raw ${p.rating.toFixed(1)}, ${fmtNum(p.reviews)} reviews)</span>
          ${pLabel !== "?" ? `<span class="result-price">${pLabel}</span>` : ""}
          ${tLabel ? `<span class="type-badge">${tLabel}</span>` : ""}
          ${isGem ? `<span class="gem-badge">few reviews</span>` : ""}
        </div>
        ${state.view === "best" && !isGem
          ? `<div class="result-score-bar"><div class="result-score-fill" style="width:${pct}%"></div></div>
             <div class="score-breakdown">★ ${rPct}%&ensp;·&ensp;📍 ${cPct}%</div>`
          : ""}
        ${attribution}
      </div>
    </div>`;
  }).join("");

  // Hover sync: list ↔ map
  list.querySelectorAll(".result-item").forEach(el => {
    el.addEventListener("mouseenter", () => {
      state.hover = el.dataset.id;
      renderLayers(lastScored);
    });
    el.addEventListener("mouseleave", () => {
      state.hover = null;
      renderLayers(lastScored);
    });
    el.addEventListener("click", () => {
      const place = items.find(p => p.id === el.dataset.id);
      if (!place) return;
      state.selected = el.dataset.id;
      state.hover = el.dataset.id;
      renderLayers(lastScored);
      if (maplib) {
        const bounds = maplib.getBounds();
        if (!bounds.contains([place.lon, place.lat])) {
          flyTo(place.lon, place.lat);
        }
      }
    });
  });
}

function escHtml(s) {
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}
function fmtNum(n) {
  if (n >= 1000) return (n / 1000).toFixed(1) + "k";
  return String(n);
}
function fmtDist(m) {
  if (m >= 1000) return (m / 1000).toFixed(1) + " km";
  return Math.round(m) + " m";
}

/* ══════════════════════════════════════════════════════════════════════════
   CORE RENDER CYCLE
   ══════════════════════════════════════════════════════════════════════════ */

function render() {
  pushStateDebounced();
  if (!state.pin) {
    lastDistArr = null;
    lastGraph   = null;
    renderLayers([]);
    renderList([]);
    setStatus("idle", "Click the map to drop a pin");
    return;
  }

  const graph = graphs[state.mode];
  const adj   = adjLists[state.mode];
  if (!graph || !adj) return;

  setStatus("routing", "Routing…");

  // Snap pin to nearest node; measure off-graph leg in seconds
  const t_nn = performance.now();
  const startNode  = nearestNodeGrid(gridLists[state.mode], graph.nodeFlat, state.pin.lat, state.pin.lon);
  console.log(`nearest-node: ${(performance.now()-t_nn).toFixed(2)} ms`);

  // No node within reach: the pin is off the network entirely. Say so, rather
  // than routing from an arbitrary node and inventing travel times.
  if (startNode < 0) {
    lastDistArr = null;
    lastGraph   = null;
    renderLayers([]);
    renderList([]);
    setStatus("error",
      `No ${state.mode === "walk" ? "walking" : "cycling"} network here — try a pin on a street`);
    return;
  }
  const cutoffSec  = state.cutoff * 60;
  const speedMps   = state.mode === "walk" ? WALK_MPS : BIKE_MPS;
  const pinSnapSec = haversineM(state.pin.lat, state.pin.lon,
    graph.nodeFlat[startNode*2], graph.nodeFlat[startNode*2+1]) / speedMps;

  // Run Dijkstra (synchronous; fast enough for this graph size)
  const distArr = dijkstra(adjLists[state.mode], graph.nodeFlat.length >> 1, startNode, cutoffSec);
  lastDistArr   = distArr;
  lastGraph     = graph;
  const scored  = filterAndScore(distArr, cutoffSec, pinSnapSec);
  lastScored    = scored;

  const count = state.view === "gems" ? hiddenGems(scored).length : scored.length;
  setStatus("done", `${count} place${count !== 1 ? "s" : ""} within ${state.cutoff} min`);

  renderLayers(scored);
  renderList(scored);
}

/* ══════════════════════════════════════════════════════════════════════════
   UI HELPERS
   ══════════════════════════════════════════════════════════════════════════ */

function setStatus(type, msg) {
  const el = document.getElementById("pin-status");
  el.className = `status-pill status-${type}`;
  el.innerHTML = msg;
  const mob = document.getElementById("pin-status-mobile");
  if (mob) { mob.className = `status-pill status-${type}`; mob.innerHTML = msg; }
}

function flyTo(lon, lat) {
  if (maplib) maplib.flyTo({ center: [lon, lat], zoom: Math.max(14, maplib.getZoom()), speed: 1.4 });
}

/* ══════════════════════════════════════════════════════════════════════════
   DATA LOADING
   ══════════════════════════════════════════════════════════════════════════ */

async function loadGraphBinary(prefix) {
  const t0 = performance.now();
  const [nodesBuf, edgesBuf, timesBuf, meta] = await Promise.all([
    fetch(`data/${prefix}_nodes.bin`).then(r => r.arrayBuffer()),
    fetch(`data/${prefix}_edges.bin`).then(r => r.arrayBuffer()),
    fetch(`data/${prefix}_times.bin`).then(r => r.arrayBuffer()),
    fetch(`data/${prefix}_meta.json`).then(r => r.json()),
  ]);
  const nodeFlat = new Float32Array(nodesBuf);
  const edgeFlat = new Uint32Array(edgesBuf);
  const timesFlat = new Uint16Array(timesBuf);
  const parseMs = (performance.now() - t0).toFixed(1);
  console.log(`[mtl-food] ${prefix}: ${meta.node_count} nodes, ${meta.edge_count} edges, parsed in ${parseMs} ms`);
  return { nodeFlat, edgeFlat, timesFlat, meta };
}

async function loadData() {
  setStatus("routing", `<span class="spinner-inline"></span>Loading data…`);

  const fetchJson = url => fetch(url).then(r => {
    if (!r.ok) throw new Error(`${r.status} ${url}`);
    return r.json();
  });

  const [placesData, walkData, bikeData, histData] = await Promise.all([
    fetchJson("data/places.json"),
    loadGraphBinary("walk"),
    loadGraphBinary("bike"),
    fetch("data/review_history.json").then(r => r.ok ? r.json() : {}),
  ]);

  places        = placesData.places;
  meanRating    = placesData.meta.mean_rating;
  reviewHistory = histData ?? {};

  graphs.walk   = walkData;
  graphs.bike   = bikeData;
  adjLists.walk = buildCSR(walkData.edgeFlat, walkData.timesFlat, walkData.meta.node_count);
  adjLists.bike = buildCSR(bikeData.edgeFlat, bikeData.timesFlat, bikeData.meta.node_count);
  gridLists.walk = buildNodeGrid(walkData.nodeFlat);
  gridLists.bike = buildNodeGrid(bikeData.nodeFlat);

  console.log(
    `[mtl-food] loaded: ${places.length} places | ` +
    `walk ${walkData.meta.node_count} nodes / ${walkData.meta.edge_count} edges | ` +
    `bike ${bikeData.meta.node_count} nodes / ${bikeData.meta.edge_count} edges`
  );

  assertNodeIdsValid(walkData, bikeData);
  renderDataAge(placesData.meta);
  renderMetaAttributions(placesData.meta);

  setStatus("idle", "Click the map to drop a pin");
}

/* Startup sanity check on the place → graph-node ids.

   Every place's travel time is read straight out of the Dijkstra distance array
   at its stored node id. If those ids were generated against a different build
   of the .bin graphs, each place gets a plausible but wrong time and no cutoff
   can filter it out — the failure is invisible. This catches it at load.

   The prep-side equivalent is `python prep/reannotate_places.py --check`, which
   also measures snap distances; here we can only check ranges cheaply, so a bad
   median still needs the prep script. */
function assertNodeIdsValid(walkData, bikeData) {
  const counts = { walk: walkData.meta.node_count, bike: bikeData.meta.node_count };
  const bad = { walk: 0, bike: 0 };
  let offNetwork = 0;

  for (const p of places) {
    for (const mode of ["walk", "bike"]) {
      const id = p[`${mode}_node`];
      if (id === null || id === undefined) { offNetwork++; continue; }
      if (!Number.isInteger(id) || id < 0 || id >= counts[mode]) bad[mode]++;
    }
  }

  if (bad.walk || bad.bike) {
    console.error(
      `[mtl-food] ${bad.walk} walk and ${bad.bike} bike node ids are out of range. ` +
      `places.json is out of sync with the .bin graphs — ` +
      `run: python prep/reannotate_places.py --mark-unreachable`
    );
    setStatus("error", "Map data is out of sync — results may be wrong");
    return false;
  }
  if (offNetwork) {
    console.log(`[mtl-food] ${offNetwork} place/mode pair(s) marked off-network`);
  }
  return true;
}

/* Render response-level html_attributions returned by the Places API.

   Google requires these to be displayed verbatim, including their links, so the
   markup is inserted as-is rather than escaped. That is deliberate, and it is the
   one place in this app where unescaped HTML is written to the DOM: the strings
   come from the API response, are stored untouched in places.json, and the policy
   leaves no room to sanitise them. If places.json is ever built from a source
   other than the Google API, revisit this. */
function renderMetaAttributions(meta = {}) {
  const el = document.getElementById("attribution-extra");
  if (!el) return;
  const list = Array.isArray(meta.attributions) ? meta.attributions : [];
  if (!list.length) { el.hidden = true; el.innerHTML = ""; return; }
  el.hidden = false;
  el.innerHTML = list.join(" ");
}

/* Show when the underlying data was pulled, so a stale deploy is visible. */
function renderDataAge(meta = {}) {
  const el = document.getElementById("data-age");
  if (!el) return;

  // Only pull dates count as freshness. meta.annotated is when node ids were
  // last re-snapped, which says nothing about how old the place data is.
  const stamp = meta.enriched || meta.generated;
  if (!stamp) { el.hidden = true; return; }

  const then = new Date(`${stamp}T00:00:00`);
  if (isNaN(then)) { el.hidden = true; return; }

  const days = Math.floor((Date.now() - then) / 86_400_000);
  // STALE_AFTER_DAYS is a product decision, not a compliance requirement: place
  // data drifts (closures, hours, ratings) and a month is about how long this
  // corpus stays trustworthy. Google's caching terms do constrain how long Places
  // content may be retained, but a general retention window could not be verified
  // from the published policy, so this number is ours and should not be cited as
  // theirs. See DATA.md.
  const STALE_AFTER_DAYS = 30;
  const rel = days <= 0 ? "today"
            : days === 1 ? "yesterday"
            : days < 30  ? `${days} days ago`
            : days < 365 ? `${Math.floor(days / 30)} mo ago`
            : `${Math.floor(days / 365)} yr ago`;

  const stale = days > STALE_AFTER_DAYS;
  el.hidden = false;
  el.textContent = stale
    ? `Data ${rel} — stale, due a refresh`
    : `Data updated ${rel}`;
  el.title = stale
    ? `Places last pulled ${stamp}. Older than ${STALE_AFTER_DAYS} days, so ratings, `
      + `hours and closures may be out of date. Re-run the pipeline (see README).`
    : `Places last pulled ${stamp}`;
  el.classList.toggle("stale", stale);
}

/* ══════════════════════════════════════════════════════════════════════════
   MAP CLICK / PIN DROP
   ══════════════════════════════════════════════════════════════════════════ */

function handleMapClick({ coordinate }) {
  if (!coordinate) return;
  state.pin = { lon: coordinate[0], lat: coordinate[1] };
  state.selected = null;
  state.search = "";
  const sb = document.getElementById("search-bar");
  if (sb) sb.value = "";
  render();
}

/* ══════════════════════════════════════════════════════════════════════════
   INIT
   ══════════════════════════════════════════════════════════════════════════ */

function initMap() {
  // MapLibre base map (non-interactive; deck.gl drives pan/zoom)
  maplib = new maplibregl.Map({
    container: "map",
    style: BASEMAPS["ofm-liberty"],
    center: [INITIAL_VIEW.longitude, INITIAL_VIEW.latitude],
    zoom: INITIAL_VIEW.zoom,
    interactive: false,
    attributionControl: false,
  });
  maplib.addControl(new maplibregl.AttributionControl({ compact: true }), "bottom-right");

  // deck.gl overlay
  deckgl = new deck.Deck({
    canvas: "deck-canvas",
    width: "100%",
    height: "100%",
    initialViewState: INITIAL_VIEW,
    controller: true,
    onViewStateChange: ({ viewState }) => {
      maplib.jumpTo({
        center:  [viewState.longitude, viewState.latitude],
        zoom:    viewState.zoom,
        bearing: viewState.bearing,
        pitch:   viewState.pitch,
      });
    },
    onClick: handleMapClick,
    getTooltip: ({ object }) => {
      if (!object) return null;
      const p      = object;
      const pLabel = priceStr(p.price);
      const tLabel = typeLabel(p.types);
      const tMin   = p.travelMin != null ? `${p.travelMin.toFixed(0)} min` : "";
      const dist   = p.distM != null ? fmtDist(p.distM) : "";
      return {
        html: `
          <strong>${escHtml(p.name)}</strong>
          ${tLabel ? `<span class="tt-type">${tLabel}</span>` : ""}<br/>
          <span class="tt-stars">★ ${p.rating.toFixed(1)}</span>
          <span class="tt-muted">(${fmtNum(p.reviews)} reviews)</span>
          ${pLabel !== "?" ? `&nbsp;·&nbsp;<span class="tt-price">${pLabel}</span>` : ""}
          ${tMin ? `<br/><span class="tt-muted">${modeIcon()} ${tMin}${dist ? " · " + dist : ""}</span>` : ""}`,
        className: "deck-tooltip",
        style: {},
      };
    },
    layers: [],
  });
}

function initControls() {
  // Mobile filters toggle
  const filtersBtn = document.getElementById("btn-filters-toggle");
  const controlsPanel = document.getElementById("controls-panel");
  if (filtersBtn && controlsPanel) {
    filtersBtn.addEventListener("click", () => {
      const open = controlsPanel.classList.toggle("open");
      filtersBtn.classList.toggle("open", open);
      filtersBtn.textContent = open ? "Filters ▴" : "Filters ▾";
    });
  }

  // Mode toggle
  document.querySelectorAll(".toggle-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      state.mode = btn.dataset.mode;
      document.querySelectorAll(".toggle-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      render();
    });
  });

  // Cutoff slider
  const cutoffSlider = document.getElementById("slider-cutoff");
  const cutoffDisplay = document.getElementById("cutoff-display");
  cutoffSlider.addEventListener("input", () => {
    state.cutoff = +cutoffSlider.value;
    cutoffDisplay.textContent = `${state.cutoff} min`;
    render();
  });

  // Weight slider
  const weightSlider = document.getElementById("slider-weight");
  updateWeightLabel();
  weightSlider.addEventListener("input", () => {
    state.w = +weightSlider.value;
    updateWeightLabel();
    render();
  });

  // Category chips
  document.querySelectorAll(".cat-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      state.category = btn.dataset.cat || null;
      document.querySelectorAll(".cat-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      render();
    });
  });

  // Price buttons — click to include, click again to remove; empty = all
  document.querySelectorAll(".price-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const p = +btn.dataset.price;
      if (state.prices.has(p)) {
        state.prices.delete(p);
        btn.classList.remove("active");
      } else {
        state.prices.add(p);
        btn.classList.add("active");
      }
      render();
    });
  });

  // View tabs
  document.getElementById("tab-best").addEventListener("click", () => {
    state.view = "best";
    document.getElementById("tab-best").classList.add("active");
    document.getElementById("tab-gems").classList.remove("active");
    render();
  });
  document.getElementById("tab-gems").addEventListener("click", () => {
    state.view = "gems";
    document.getElementById("tab-gems").classList.add("active");
    document.getElementById("tab-best").classList.remove("active");
    render();
  });

  // Open now toggle
  const btnOpenNow = document.getElementById("btn-open-now");
  btnOpenNow.addEventListener("click", () => {
    state.openNow = !state.openNow;
    btnOpenNow.classList.toggle("active", state.openNow);
    render();
  });

  // Search bar
  document.getElementById("search-bar").addEventListener("input", e => {
    state.search = e.target.value;
    pushStateDebounced();
    renderList(lastScored);
  });

  // Basemap switcher
  document.getElementById("basemap-select").addEventListener("change", e => {
    const url = BASEMAPS[e.target.value];
    if (url && maplib) maplib.setStyle(url);
  });

  // Welcome modal
  const overlay = document.getElementById("welcome-overlay");
  const closeModal = () => overlay.classList.add("hidden");
  document.getElementById("welcome-close").addEventListener("click", closeModal);
  overlay.addEventListener("click", e => { if (e.target === overlay) closeModal(); });
  document.getElementById("btn-help").addEventListener("click", () => overlay.classList.remove("hidden"));

  // Copy link
  document.getElementById("btn-copy-link").addEventListener("click", () => {
    const qs = serializeState().toString();
    history.replaceState(null, "", qs ? `?${qs}` : location.pathname);
    navigator.clipboard.writeText(location.href).then(() => {
      const btn = document.getElementById("btn-copy-link");
      const orig = btn.textContent;
      btn.textContent = "✓ Copied!";
      setTimeout(() => btn.textContent = orig, 1500);
    });
  });

  // Geolocation
  document.getElementById("btn-locate").addEventListener("click", () => {
    if (!navigator.geolocation) return;
    setStatus("routing", "Getting location…");
    navigator.geolocation.getCurrentPosition(
      pos => {
        const { latitude: lat, longitude: lon } = pos.coords;
        state.pin = { lat, lon };
        flyTo(lon, lat);
        render();
      },
      () => setStatus("idle", "Location access denied")
    );
  });
}

/* ── Hours self-tests (results logged to console on load) ────────────────── */
function _testHours() {
  const assert = (desc, got, want) => {
    const ok = got === want;
    console[ok ? "log" : "error"](`${ok ? "✓" : "✗"} hours: ${desc}  (got ${got}, want ${want})`);
  };
  // Normal 11:00–22:00, Monday
  const normal = [[1, 1100, 1, 2200]];
  assert("normal inside",       checkHours(normal, 1, 1300), true);
  assert("normal before open",  checkHours(normal, 1, 1000), false);
  assert("normal at close",     checkHours(normal, 1, 2200), false);
  assert("normal wrong day",    checkHours(normal, 2, 1300), false);
  // Overnight: Fri 22:00 → Sat 02:00
  const overnight = [[5, 2200, 6, 200]];
  assert("overnight Fri 23:00", checkHours(overnight, 5, 2300), true);
  assert("overnight Sat 01:00", checkHours(overnight, 6,  100), true);
  assert("overnight Sat 02:00", checkHours(overnight, 6,  200), false);
  assert("overnight Sat 10:00", checkHours(overnight, 6, 1000), false);
  // Sun→Mon wraparound: Sun 23:00 → Mon 03:00
  const wrap = [[0, 2300, 1, 300]];
  assert("wrap Sun 23:30",      checkHours(wrap, 0, 2330), true);
  assert("wrap Mon 02:00",      checkHours(wrap, 1,  200), true);
  assert("wrap Mon 03:00",      checkHours(wrap, 1,  300), false);
  // 24/7
  const always = [[0, 0, -1, -1]];
  assert("24/7",                checkHours(always, 3, 300), true);
  // No hours → null
  assert("null hours",          checkHours(null,   1, 1200), null);
  assert("empty hours",         checkHours([],     1, 1200), null);
}

/* ── Results panel vertical resize ──────────────────────────────────────── */
function initResultsResizer() {
  const resizer  = document.getElementById("results-resizer");
  const controls = document.getElementById("controls-panel");
  if (!resizer || !controls) return;

  let startY, startH;

  resizer.addEventListener("mousedown", e => {
    startY = e.clientY;
    startH = controls.offsetHeight;
    resizer.classList.add("dragging");
    document.body.style.cursor = "row-resize";
    document.body.style.userSelect = "none";

    function onMove(e) {
      const h = Math.min(Math.max(startH + (e.clientY - startY), 80), window.innerHeight - 120);
      controls.style.height = h + "px";
    }
    function onUp() {
      resizer.classList.remove("dragging");
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  });
}

/* ── Sidebar resize handle ───────────────────────────────────────────────── */
function initSidebarResizer() {
  const resizer = document.getElementById("sidebar-resizer");
  const sidebar = document.getElementById("sidebar");
  if (!resizer || !sidebar) return;

  let startX, startW;

  resizer.addEventListener("mousedown", e => {
    startX = e.clientX;
    startW = sidebar.offsetWidth;
    resizer.classList.add("dragging");
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";

    function onMove(e) {
      const w = Math.min(Math.max(startW + (e.clientX - startX), 280), 700);
      sidebar.style.width = w + "px";
      document.documentElement.style.setProperty("--sidebar-w", w + "px");
    }
    function onUp() {
      resizer.classList.remove("dragging");
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  });
}

/* ── Boot ────────────────────────────────────────────────────────────────── */
window.addEventListener("DOMContentLoaded", async () => {
  _testHours();
  parseStateFromURL();
  initMap();
  initControls();
  initResultsResizer();
  initSidebarResizer();
  syncUIFromState();

  try {
    await loadData();
    if (state.pin) {
      flyTo(state.pin.lon, state.pin.lat);
      render();
    }
  } catch (err) {
    console.error("Data load failed:", err);
    const el = document.getElementById("pin-status");
    el.className = "status-pill status-routing";
    el.innerHTML = `Data not found — run prep scripts first`;
    document.getElementById("results-list").innerHTML =
      `<div class="results-empty">
        Run <code>pull_places.py</code> then <code>build_networks.py</code>
        to generate <code>viz/data/</code>, then serve with
        <code>python3 -m http.server 8000</code>.
      </div>`;
  }
});
