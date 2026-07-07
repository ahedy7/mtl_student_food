/* ─────────────────────────────────────────────────────────────────────────
   Mtl Food  –  client-side app
   Architecture: load data → build adj lists → Dijkstra on pin drop → score
   ───────────────────────────────────────────────────────────────────────── */

const INITIAL_VIEW = { longitude: -73.5752, latitude: 45.5088, zoom: 13, pitch: 0, bearing: 0 };
const MAP_STYLE    = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";
const PRICE_LABELS = { 0: "?", 1: "$", 2: "$$", 3: "$$$", 4: "$$$$" };

/* ── State ───────────────────────────────────────────────────────────────── */
const state = {
  mode:    "walk",
  cutoff:  20,        // minutes
  prices:  new Set([0, 1, 2, 3, 4]),
  w:       0.6,       // quality weight (0 = all closeness, 1 = all quality)
  view:    "best",    // "best" | "gems"
  pin:     null,      // { lat, lon }
  hover:   null,      // place id currently hovered
};

/* ── Data ────────────────────────────────────────────────────────────────── */
let places      = [];
let meanRating  = 0;
let graphs      = { walk: null, bike: null };
let adjLists    = { walk: null, bike: null };
let lastScored  = [];

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

function buildAdj(graph) {
  const n   = graph.nodes.length;
  const adj = new Array(n);
  for (let i = 0; i < n; i++) adj[i] = [];
  for (const [from, to, sec] of graph.edges) {
    adj[from].push([to, sec]);
    adj[to].push([from, sec]);
  }
  return adj;
}

function nearestNodeIdx(nodes, lat, lon) {
  let bestD = Infinity, bestI = 0;
  for (let i = 0; i < nodes.length; i++) {
    const dlat = nodes[i][0] - lat;
    const dlon = nodes[i][1] - lon;
    const d = dlat * dlat + dlon * dlon;
    if (d < bestD) { bestD = d; bestI = i; }
  }
  return bestI;
}

/* Dijkstra single-source, capped at cutoffSec.
   Returns Float64Array of travel times (Infinity = unreachable/over cap). */
function dijkstra(adj, start, cutoffSec) {
  const dist = new Float64Array(adj.length).fill(Infinity);
  dist[start] = 0;
  const heap = new MinHeap();
  heap.push([0, start]);
  while (heap.size > 0) {
    const [d, u] = heap.pop();
    if (d > dist[u]) continue;
    if (d > cutoffSec) break;
    const nbrs = adj[u];
    for (let i = 0; i < nbrs.length; i++) {
      const [v, w] = nbrs[i];
      const nd = d + w;
      if (nd < dist[v]) {
        dist[v] = nd;
        heap.push([nd, v]);
      }
    }
  }
  return dist;
}

/* ══════════════════════════════════════════════════════════════════════════
   SCORING
   ══════════════════════════════════════════════════════════════════════════ */

function filterAndScore(distArr, cutoffSec) {
  const cutoffMin = cutoffSec / 60;
  const m = 50;    // Bayesian prior weight
  const C = meanRating;
  const nodeKey = state.mode === "walk" ? "walk_node" : "bike_node";

  // Hard filters: reachability + price
  let survivors = places.filter(p => {
    const t = distArr[p[nodeKey]];
    return t !== undefined && t <= cutoffSec && state.prices.has(p.price ?? 0);
  }).map(p => ({
    ...p,
    travelMin: distArr[p[nodeKey]] / 60,
  }));

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

  return survivors.sort((a, b) => b.score - a.score);
}

function hiddenGems(survivors) {
  const highRated = survivors.filter(p => p.rating >= 4.3);
  if (!highRated.length) return [];
  const counts = survivors.map(p => p.reviews).sort((a, b) => a - b);
  const q1 = counts[Math.floor(counts.length * 0.25)];
  return highRated
    .filter(p => p.reviews <= q1)
    .sort((a, b) => b.rating - a.rating);
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
  const { ScatterplotLayer } = deck;

  const viewPlaces = state.view === "gems" ? hiddenGems(scored) : scored;

  const restaurants = new ScatterplotLayer({
    id: "restaurants",
    data: viewPlaces,
    getPosition: d => [d.lon, d.lat],
    getRadius: d => 5 + d.score * 9,
    getFillColor: d => state.view === "gems" ? gemColor() : scoreToColor(d.score),
    getLineColor: d => state.hover === d.id ? [255, 255, 255, 200] : [255, 255, 255, 40],
    lineWidthMinPixels: 1,
    stroked: true,
    radiusUnits: "pixels",
    pickable: true,
    autoHighlight: false,
    updateTriggers: {
      getFillColor: [state.view, state.hover],
      getLineColor: [state.hover],
    },
  });

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

  deckgl.setProps({ layers: [restaurants, pinOuter, pinInner] });
}

/* ══════════════════════════════════════════════════════════════════════════
   RESULTS LIST
   ══════════════════════════════════════════════════════════════════════════ */

function priceStr(price) { return PRICE_LABELS[price] ?? "?"; }
function modeIcon()      { return state.mode === "walk" ? "🚶" : "🚴"; }

function renderList(scored) {
  const list  = document.getElementById("results-list");
  const items = state.view === "gems" ? hiddenGems(scored) : scored;

  if (!items.length) {
    list.innerHTML = `<div class="results-empty">No restaurants found within ${state.cutoff} min. Try increasing the cutoff or moving the pin.</div>`;
    return;
  }

  list.innerHTML = items.slice(0, 60).map((p, i) => {
    const isTop  = i < 3 && state.view === "best";
    const isGem  = state.view === "gems";
    const pct    = Math.round(p.score * 100);
    const tMin   = p.travelMin.toFixed(0);
    const pLabel = priceStr(p.price);

    return `<div class="result-item" data-id="${p.id}" tabindex="0">
      <div class="result-rank ${isTop ? "top" : ""}">${isGem ? "◆" : i + 1}</div>
      <div class="result-body">
        <div class="result-name">${escHtml(p.name)}</div>
        <div class="result-meta">
          <span class="result-rating">★ ${p.rating.toFixed(1)}<span class="result-rating-count">(${fmtNum(p.reviews)})</span></span>
          ${pLabel !== "?" ? `<span class="result-price">${pLabel}</span>` : ""}
          <span class="result-time">${modeIcon()} ${tMin} min</span>
          ${isGem ? `<span class="gem-badge">few reviews</span>` : ""}
        </div>
        ${state.view === "best"
          ? `<div class="result-score-bar"><div class="result-score-fill" style="width:${pct}%"></div></div>`
          : ""}
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
      if (place) flyTo(place.lon, place.lat);
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

/* ══════════════════════════════════════════════════════════════════════════
   CORE RENDER CYCLE
   ══════════════════════════════════════════════════════════════════════════ */

function render() {
  if (!state.pin) {
    renderLayers([]);
    renderList([]);
    setStatus("idle", "Click the map to drop a pin");
    return;
  }

  const graph = graphs[state.mode];
  const adj   = adjLists[state.mode];
  if (!graph || !adj) return;

  setStatus("routing", "Routing…");

  // Snap pin to nearest node
  const startNode = nearestNodeIdx(graph.nodes, state.pin.lat, state.pin.lon);
  const cutoffSec = state.cutoff * 60;

  // Run Dijkstra (synchronous; fast enough for this graph size)
  const distArr = dijkstra(adj, startNode, cutoffSec);
  const scored  = filterAndScore(distArr, cutoffSec);
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
}

function flyTo(lon, lat) {
  if (maplib) maplib.flyTo({ center: [lon, lat], zoom: Math.max(14, maplib.getZoom()), speed: 1.4 });
}

/* ══════════════════════════════════════════════════════════════════════════
   DATA LOADING
   ══════════════════════════════════════════════════════════════════════════ */

async function loadData() {
  setStatus("routing", `<span class="spinner-inline"></span>Loading data…`);

  const fetchJson = url => fetch(url).then(r => {
    if (!r.ok) throw new Error(`${r.status} ${url}`);
    return r.json();
  });

  const [placesData, walkData, bikeData] = await Promise.all([
    fetchJson("data/places.json"),
    fetchJson("data/walk_graph.json"),
    fetchJson("data/bike_graph.json"),
  ]);

  places     = placesData.places;
  meanRating = placesData.meta.mean_rating;

  graphs.walk   = walkData;
  graphs.bike   = bikeData;
  adjLists.walk = buildAdj(walkData);
  adjLists.bike = buildAdj(bikeData);

  console.log(
    `[mtl-food] loaded: ${places.length} places | ` +
    `walk ${walkData.nodes.length} nodes / ${walkData.edges.length} edges | ` +
    `bike ${bikeData.nodes.length} nodes / ${bikeData.edges.length} edges`
  );

  setStatus("idle", "Click the map to drop a pin");
}

/* ══════════════════════════════════════════════════════════════════════════
   MAP CLICK / PIN DROP
   ══════════════════════════════════════════════════════════════════════════ */

function handleMapClick({ coordinate }) {
  if (!coordinate) return;
  state.pin = { lon: coordinate[0], lat: coordinate[1] };
  render();
}

/* ══════════════════════════════════════════════════════════════════════════
   INIT
   ══════════════════════════════════════════════════════════════════════════ */

function initMap() {
  // MapLibre base map (non-interactive; deck.gl drives pan/zoom)
  maplib = new maplibregl.Map({
    container: "map",
    style: MAP_STYLE,
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
      const tMin   = p.travelMin != null ? `${p.travelMin.toFixed(0)} min` : "";
      return {
        html: `
          <strong>${escHtml(p.name)}</strong><br/>
          ★ ${p.rating.toFixed(1)} &nbsp;·&nbsp; ${fmtNum(p.reviews)} reviews
          ${pLabel !== "?" ? `&nbsp;·&nbsp; ${pLabel}` : ""}
          ${tMin ? `<br/><small>${modeIcon()} ${tMin}</small>` : ""}`,
        className: "deck-tooltip",
        style: {},
      };
    },
    layers: [],
  });
}

function initControls() {
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
  const weightDisplay = document.getElementById("weight-display");
  weightSlider.addEventListener("input", () => {
    state.w = +weightSlider.value;
    weightDisplay.textContent = state.w.toFixed(2);
    render();
  });

  // Price buttons
  document.querySelectorAll(".price-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const p = +btn.dataset.price;
      if (state.prices.has(p)) {
        // Always keep at least one price selected
        if (state.prices.size > 1) {
          state.prices.delete(p);
          btn.classList.remove("active");
        }
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

/* ── Boot ────────────────────────────────────────────────────────────────── */
window.addEventListener("DOMContentLoaded", async () => {
  initMap();
  initControls();

  try {
    await loadData();
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
