# Data provenance, licensing, and refresh

This project combines two data sources with different licences and different
obligations. This document states which is which, what each requires, and what
this project deliberately does not do.

Written to be read by someone evaluating the project, not just by someone running
it. Where a requirement could not be verified against published terms, that is
said plainly rather than smoothed over.

**Not legal advice.** Terms change; the summaries below were checked against live
documentation on 2026-08-20 and each links to its source.

---

## What comes from where

| Field in `places.json` | Source | Notes |
|---|---|---|
| `id` (place ID) | Google Places | The one field explicitly exempt from caching limits |
| `name` | Google Places | |
| `lat` / `lon` | Google Places | |
| `rating` | Google Places | |
| `reviews` (count) | Google Places | Count only — no review text, ever |
| `price` | Google Places | |
| `types` | Google Places | Drives the category filters |
| `hours` | Google Places | Via Place Details, compacted to weekly periods |
| `recent_share`, `review_sample` | Derived from Google Places | Computed from review *timestamps*; no text retained |
| `attributions` | Google Places | `html_attributions`, displayed verbatim when present |
| `walk_node`, `bike_node`, `*_snap_m` | Computed locally | Indices into the OSM graphs |
| `score`, `closeness`, `_shrunkR` | Computed locally | Ranking, computed in the browser at query time |

| File | Source |
|---|---|
| `walk_*.bin`, `bike_*.bin` | OpenStreetMap via [osmnx](https://osmnx.readthedocs.io/) |
| Basemap tiles | [OpenFreeMap](https://openfreemap.org/), [Carto](https://carto.com/), [Stadia Maps](https://stadiamaps.com/) — selectable at runtime |

OSM data is © OpenStreetMap contributors, available under the
[Open Database Licence](https://www.openstreetmap.org/copyright).

---

## Google Places: what the policy requires

### Attribution

Places content here is displayed on a **MapLibre map over OpenStreetMap tiles**, not
a Google map. The [Places API policies](https://developers.google.com/maps/documentation/places/web-service/policies)
address this case directly:

> "When displaying Places API data without a Google Map, you must include the
> Google logo, adhering to the provided style guidelines and attribution
> requirements."

> "Attribution should take the form of the Google Maps logo whenever possible. In
> cases where space is limited, the text **Google Maps** is acceptable."

The sidebar footer is space-limited, so this project currently uses the permitted
**text form**, unmodified and unlocalized. Swapping in the official logo asset is a
one-line change — see the `GM-MARK` comment in `viz/index.html`; the CSS already
implements the required 16–19dp height and 10dp/5dp clear space.

The policy also requires attribution to sit **in its own visual container**,
distinguished from surrounding content, and not merged into another credit line.
Hence the bordered `.attribution` block rather than a line appended to the
OpenStreetMap credit.

### `html_attributions`

Returned on every call in both the legacy and current APIs, and must be displayed
verbatim including links when non-empty. The pipeline carries the field from both
Nearby Search and Place Details into `places.json`, and the app renders it — at the
result-set level in the footer, and per place on the result card.

In practice it is **empty in all 282 banked Nearby Search responses**. It is carried
through anyway so a future non-empty value is displayed rather than silently
dropped.

### Caching and retention — what is and isn't confirmed

**Confirmed:** `place_id` is exempt from caching restrictions and may be stored
indefinitely. ([Service Specific Terms](https://cloud.google.com/maps-platform/terms/maps-service-terms))

**Confirmed:** latitude/longitude from the **Places UI Kit** may be cached up to 30
consecutive calendar days. This project does not use the Places UI Kit, so that
clause does not govern it.

**Not confirmed:** a general retention window for other Places content — ratings,
review counts, hours, price levels. The terms restrict pre-fetching, caching, and
storing Places content beyond permitted exceptions, but an attempt to read the full
No-Caching clause returned a truncated page, and **no general window was verified.**

An earlier draft of this project's notes asserted "30 days for all Places content."
That was not supported by anything verified and has been removed.

**What this means honestly:** this project commits a `places.json` containing Google
Places content to a public repository and serves it from GitHub Pages. That sits in
tension with the storage restrictions, and adding a logo does not resolve it. The
30-day refresh below is a good-faith posture, not a demonstrated compliance
position. Anyone forking this for anything beyond a portfolio piece should read the
terms themselves and decide deliberately.

### Reviews: a line this project does not cross

The policy requires crediting the author whenever review text or photos are
displayed, including the author's avatar.

**This project displays neither, by design.** It stores review *counts* and a
`recent_share` computed from review *timestamps* — never review text, author names,
avatars, or photos. That keeps it clear of the author-attribution requirements
entirely, and avoids republishing individuals' writing.

If anyone extends this to show review snippets, that changes: author name, avatar,
and per-review attribution all become required, and `enrich_places.py` would need to
retain and display fields it currently discards.

---

## Refresh cadence: 30 days

**This is a product decision, not a compliance requirement.**

Restaurants close, change hours, and accumulate ratings. A month is roughly how long
this corpus stays trustworthy, so the freshness badge in the sidebar turns amber and
reads "stale, due a refresh" past 30 days. The threshold is `STALE_AFTER_DAYS` in
`viz/app.js`.

It is *not* derived from Google's caching terms, because no general window could be
verified. Do not cite it as such.

To refresh:

```bash
python prep/pull_places.py        # re-fetches; skips anything already banked
python prep/build_networks.py     # only if the bbox changed
python prep/enrich_places.py      # hours + recency
```

---

## Reproducibility: a real limitation

The pipeline uses the **legacy** Places endpoints (`/maps/api/place/nearbysearch/json`,
`/maps/api/place/details/json`). Per [Legacy products and features](https://developers.google.com/maps/legacy):

> "Legacy-marked services will retain full support."
> "[They] will not be available in new Cloud projects but remain fully supported for
> existing projects."

So these endpoints work for the Cloud project this was built against, but **a new
contributor could not reproduce the pull** — a fresh Cloud project cannot enable
them. Reproducing it would mean porting to Places API (New), which uses POST with
required field masks and a different response shape.

Google commits to **at least 12 months' notice** before decommissioning legacy
services, and no turndown date has been announced. Places and Routes entered Legacy
status on 2025-03-01.

This is why the raw response bank matters: `prep/data/raw_responses.jsonl` holds
every response verbatim, so `places.json` can be rebuilt offline for free even by
someone who cannot make the calls. See "Backing up the raw bank" in the README.

The bank is **deliberately not committed** — it is raw Google content in a public
repo, and an append-only file that grows every pull is a poor fit for git history.

---

## Summary of obligations

| Obligation | Status |
|---|---|
| Google Maps attribution on a non-Google map | Met, via the permitted text form |
| Attribution in its own visual container | Met |
| `html_attributions` displayed verbatim | Met (currently empty in all responses) |
| Author credit for review text / photos | N/A — neither is displayed or stored |
| `place_id` storage | Permitted indefinitely |
| Other Places content retention | **Unresolved.** No general window verified; see above |
| OpenStreetMap attribution | Met, via the basemap and the sidebar credit |
