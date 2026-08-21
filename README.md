# mtl_student_food

Find the best food you can actually reach from where you are in Montreal — routed
over the real walking and cycling street network, not straight-line distance.

Drop a pin, pick walk or bike and a time budget, and the app runs Dijkstra in your
browser over an OpenStreetMap graph, then ranks what's reachable by a blend of
proximity and rating.

**Static site, no backend, no build step, no framework.** `viz/` is plain HTML +
CSS + vanilla JS, deployed to GitHub Pages straight from `main`. `prep/` is an
offline Python pipeline you run by hand; its output is committed data files.

---

## Layout

```
prep/                  offline pipeline (Python, run manually)
  pull_places.py         Google Places -> viz/data/places.json          [PAID]
  build_networks.py      OSM -> walk/bike graphs + node annotation      [free]
  reannotate_places.py   re-snap places / verify committed data         [free]
  enrich_places.py       hours + review recency                         [PAID]
  track_reviews.py       review-count snapshots for velocity            [free by default]
  snap.py                shared snapping + bound checks (imported, not run)
  graph.py               binary graph loader + Dijkstra, for verification
viz/                   the app — this directory is what gets deployed
  index.html app.js style.css
  data/                  committed output of prep/
.github/workflows/
  deploy.yml             push to main -> GitHub Pages
  track_reviews.yml      manual trigger only (no cron, no API spend)
```

---

## Run order

Run these in order. Only the first and fourth cost money.

```bash
cd prep
pip install -r requirements.txt
export GOOGLE_PLACES_API_KEY=your_key_here     # PowerShell: $env:GOOGLE_PLACES_API_KEY="..."

python pull_places.py          # 1. fetch places          ~$2.69   PAID
python build_networks.py       # 2. build graphs + snap    free
python enrich_places.py        # 3. hours + recency       ~$4.40   PAID
```

Then commit `viz/data/` and push — `deploy.yml` publishes to Pages on every push
to `main`.

### 1. `pull_places.py` — places [PAID]

Nearby Search over a list of neighbourhood centres, deduplicated by place id.
Drops anything with no rating or fewer than 5 reviews.

Paginates to Google's hard ceiling of **60 results per (centre, type)** — 3 pages
of 20, with the required 2 s pause between page-token requests. That ceiling
matters: a centre covering more than ~60 restaurants returns only the 60 most
*prominent* ones, so the long tail is silently dropped. Since "Hidden gems" is
built from exactly that tail, dense areas need more, tighter centres rather than
fewer, wider ones.

| Flag | Effect |
|---|---|
| `--yes` | Skip the cost confirmation prompt |

Cost: `centres × 2 types × 3 pages × $0.032`. With the current 14 centres that is
84 calls ≈ **$2.69**. The script prints the estimate and waits for confirmation.

### Backing up the raw bank

Every API response is appended verbatim to `prep/data/raw_responses.jsonl` before
anything parses it. That file is the only artefact in this project that cost money
and cannot be regenerated locally — `places.json` is a pure transform over it, so
re-parsing is free, but re-fetching is not.

**It is gitignored on purpose**, for two reasons: it holds raw Google Places
content, which is subject to caching and redistribution restrictions and this repo
is public; and an append-only file that grows with every pull is a poor fit for git
history. So it needs an out-of-band copy.

After every pull:

```bash
# the pull prints the path and size; copy it somewhere durable
cp prep/data/raw_responses.jsonl ~/backups/mtl_food/raw_responses_$(date +%Y%m%d).jsonl
```

To rebuild `places.json` from a restored bank — free, no network, no key needed
beyond the env check:

```bash
python prep/pull_places.py        # skips every banked query, rebuilds and exits
```

A run with nothing left to fetch reports `Will fetch: 0` and goes straight to the
rebuild. `--force` re-fetches everything and costs full price.

### 2. `build_networks.py` — street graphs [free]

Downloads walk and bike networks from OSM via `osmnx`, writes binary typed-array
files the browser routes on, and re-snaps every place to its nearest graph node.

```
{prefix}_nodes.bin   Float32 [lat0, lon0, lat1, lon1, ...]
{prefix}_edges.bin   Uint32  [from0, to0, from1, to1, ...]
{prefix}_times.bin   Uint16  seconds per edge, clamped to 65535
{prefix}_meta.json   node/edge counts, bbox, speeds
```

| Flag | Effect |
|---|---|
| `--convert` | Convert existing `*_graph.json` to binary instead of downloading |
| `--mark-unreachable` | Record places >500 m from a network as unreachable for that mode instead of failing |

Free — OSM and Overpass cost nothing. Budget 5–15 minutes and a few hundred MB of
RAM for the download.

**Re-annotation is not optional.** Writing new `.bin` files renumbers every node,
which invalidates every `walk_node`/`bike_node` in `places.json`. Both the normal
and the `--convert` path re-snap automatically; see *Node ids* below for why this
is load-bearing.

### 3. `reannotate_places.py` — re-snap only [free]

Recomputes `walk_node` / `bike_node` / `walk_snap_m` / `bike_snap_m` against the
`.bin` files **already** in `viz/data/`. No downloads, no API calls. Use it when
the node ids have drifted but the graphs themselves are fine.

| Flag | Effect |
|---|---|
| `--check` | Verify ids against the `.bin` graphs **and** route sample pins to assert the straight-line bound; exit non-zero on either. Writes nothing — CI gate |
| `--dry-run` | Report what would change, write nothing |
| `--mark-unreachable` | Record places >500 m from a network as unreachable for that mode instead of failing |

### 4. `enrich_places.py` — hours + recency [PAID]

Fetches `opening_hours` and `reviews` in a **single** Details call per place.
Supersedes `add_hours.py` + `add_recency.py`, which made two calls for the same
data. Safe to re-run: skips places that already have both fields.

| Flag | Effect |
|---|---|
| `--limit N` | Cap the number of API calls (default **200** ≈ $4.40) |
| `--dry-run` | Show the plan, make no calls |
| `--yes` | Skip the cost confirmation prompt |

Places are prioritised most-reviewed-first, so a limit still covers what users are
most likely to see. Cost is `calls × $0.022`.

> Recency caveat: Google returns at most 5 reviews, ranked by relevance rather
> than date. `recent_share` is a rough directional signal, not a review history.

### 5. `track_reviews.py` — velocity snapshots [free by default]

Appends today's review counts to `viz/data/review_history.json`, which powers the
"heating up" badge. `pull_places.py` already calls this for free at the end of a
pull, so you rarely need it by hand.

| Flag | Effect |
|---|---|
| *(none)* | Free — reads counts already in `places.json` |
| `--api` | Fetch fresh counts from the Details API (~$0.017/call) |
| `--limit N` | Cap API calls in `--api` mode |
| `--dry-run` | Show what would run, write nothing |

---

## Cost summary

| Script | Cost | Notes |
|---|---|---|
| `pull_places.py` | ~$2.69 | 84 calls at 14 centres |
| `build_networks.py` | **free** | OSM only |
| `reannotate_places.py` | **free** | No network access at all |
| `enrich_places.py` | ~$4.40 | Default `--limit 200` |
| `track_reviews.py` | **free** | Unless `--api` |
| **Full pipeline** | **~$7.10** | |

Every paid script prints an estimate and waits for confirmation unless you pass
`--yes`. All of them are re-run safe.

---

## Coverage config

Two settings control coverage, and **they must agree**. Places outside the network
bbox have no nearby graph node, and snapping refuses to place them.

**`pull_places.py` — where places come from:**

```python
SEARCH_CENTERS = [(45.55280, -73.60873, 1131), ...]   # (lat, lon, radius_m)
PLACE_TYPES    = ["restaurant", "cafe"]
```

Radius varies per centre. Nearby Search caps at 60 results per (centre, type),
so cells are subdivided until none holds more than ~30 known places of either
type — see *Coverage sizing* below.

**`build_networks.py` — where routing is possible:**

```python
NORTH =  45.560     # Rosemont / Villeray
SOUTH =  45.445     # Verdun / Île-des-Sœurs
EAST  = -73.505     # Hochelaga
WEST  = -73.660     # NDG / Monkland
```

### Coverage sizing

Uniform centres do not work here, because the binding constraint is not area but
Google's 60-result ceiling per (centre, type). Under the previous 14-centre
layout, **13 of 14 centres were at or past that ceiling** — the densest held 178
restaurants and 116 cafés against a cap of 60 — so most of the corpus was never
retrievable at all, and what was lost was the least prominent tail.

Centres are therefore produced by recursive quadtree subdivision: start at 3200 m
squares, split any square holding more than ~30 known places of either type, floor
at 400 m. Radius is each square's half-diagonal, so circles cover their squares
with no gaps; empty squares are dropped, which removes the river, Mount Royal, and
the rail yards automatically. That yields 60 centres across four radius tiers
(2263 / 1131 / 566 / 283 m).

The threshold sits at 30 rather than 60 because the counts driving it come from an
already-truncated pull and are lower bounds. `pull_places.py` flags any query that
comes back with 60 results, so a cell that still saturates is visible immediately
and can be subdivided in a follow-up pull.

Changing either setting means re-running `pull_places.py` (paid) and
`build_networks.py` (free) — and the bbox governs data size, since node count
scales with area.

---

## Node ids, and why snapping fails loudly

Each place stores the index of its nearest graph node. The app reads that place's
travel time straight out of the Dijkstra distance array at that index.

If the index is wrong, the place still gets a **plausible-looking** travel time —
just measured from somewhere else entirely. Nothing looks broken: no error, no
missing pin, no absurd number. Places simply appear that shouldn't, and lowering
the cutoff never removes them, because the number being filtered on is itself
wrong. This shipped once: node ids were left pointing at a previous graph build,
and the median place sat **2 km** from the node it claimed.

So snapping refuses to guess:

- **No fallback to node 0.** A place that cannot be snapped raises an error
  rather than silently borrowing a stranger's location.
- **500 m hard ceiling.** Anything farther fails the run, unless you pass
  `--mark-unreachable` to record it as genuinely off that network (null node) —
  which the app treats as unreachable. Four food stands inside La Ronde are the
  real case: the walk graph reaches them, the bike graph doesn't.
- **50 m median ceiling.** The single cheapest signal that ids belong to a
  different graph. Healthy values are ~16 m walk, ~24 m bike.
- **Startup check in the app.** `assertNodeIdsValid` in `viz/app.js` flags
  out-of-range ids in the console and in the status pill.

### The straight-line bound

Stale ids are one way to get a wrong travel time; a changed binary layout, a bad
CSR build, or an off-by-one in Dijkstra are others. One invariant catches all of
them, because it doesn't depend on how the graph is stored:

> A place reachable in *t* seconds cannot be farther from the pin, as the crow
> flies, than *t* × speed. No route beats a straight line.

It's checked at 2× the mode speed — loose enough never to fire on a real result,
tight enough that the bug that shipped (a 6.3 km "10-minute walk", an implied
38 km/h) trips it 80 times over. It runs in three places:

- `check_crow_bound()` in `prep/snap.py` — the shared implementation
- `--check` routes 5 sample pins × 2 cutoffs × both modes and asserts it
- `assertCrowBound()` in `viz/app.js` — every render, in dev mode only
  (localhost, or any URL with `?debug=1`), logging violations to the console

Verify committed data at any time — free, no network, about a second:

```bash
python prep/reannotate_places.py --check
```

Exits non-zero with a diagnosis if the ids are stale or any sample pin returns a
physically impossible result. `deploy.yml` runs this as a gate before publishing,
so bad data cannot reach Pages.

---

## Data freshness

`pull_places.py` stamps `meta.generated` and `enrich_places.py` stamps
`meta.enriched` in `places.json`. The sidebar shows "Data updated N days ago"
from those, turning amber past 180 days, so a stale deploy is visible in the UI
rather than something you have to go digging for.

`meta.annotated` also gets written, by the snapping step — it records when node
ids were last recomputed and is deliberately *not* used for the freshness badge,
since re-snapping doesn't make the place data any newer.

Nothing updates on a schedule. `track_reviews.yml` is `workflow_dispatch` only —
deliberately no cron, so the repo never spends API budget on its own.

---

## Local development

```bash
cd viz
python -m http.server 8765
# open http://localhost:8765/index.html
```

Serving matters — `app.js` fetches the data files, so opening `index.html` from
`file://` will fail on CORS.

The app has no dependencies to install; MapLibre GL and deck.gl load from a CDN at
runtime, so you need a network connection but no `npm install`.

---

## How ranking works

Within the reachable set, each place is scored:

```
score = w · ratingScaled + (1 − w) · closeness
```

- `w` is the Proximity ↔ Rating slider.
- `closeness` is `1 − travelMin / cutoffMin`.
- `ratingScaled` is a Bayesian-shrunk rating, min-max scaled across survivors.
  Shrinkage pulls low-review places toward the corpus mean (prior weight 50), so
  a single 5★ review doesn't outrank a well-established favourite.

Travel time counts three legs: pin → nearest node (off-graph), the routed path,
and nearest node → place (off-graph). The off-graph legs are straight-line at the
mode's speed.

**Hidden gems** re-ranks the same survivors: rating ≥ 4.3, review count in the
bottom quartile, sorted by shrunk rating — well-loved places that haven't been
discovered yet.
