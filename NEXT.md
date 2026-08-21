# Next session — start here

Written 2026-08-20 at the end of a long session. Everything below is committed and
pushed; `main` is at `70bda61`.

---

## 1. Browser check first, before any code

The 3003-place dataset has **never been looked at in a browser**. Every check so far
has been programmatic. Do this before trusting anything.

```bash
cd viz && python -m http.server 8765
# http://localhost:8765/index.html
```

**Pins to try:**

| Pin | What to confirm |
|---|---|
| `45.5236, -73.5803` (Plateau) with **`?debug=1`** | Dev-mode `assertCrowBound` runs every render. Console must stay clean — any `[mtl-food]` error means a result violated the straight-line bound |
| A **Verdun** pin (~`45.462, -73.572`) | New southern coverage. Results should appear and be plausibly local |
| An **Île-des-Sœurs** pin (~`45.457, -73.548`) in **walk** mode | Must show *"No walking network here — try a pin on a street"*, not an empty list and not results. Switch to **bike** — that should work |
| Anywhere in the river | Same off-network message |

**Also confirm visually:**
- Attribution block at the sidebar bottom: its own bordered container, "Place data —
  Google Maps" above "Map & routing — OpenStreetMap contributors, OpenFreeMap"
- Freshness badge reads "Data updated today" (pull was 2026-08-20); it turns amber
  past 30 days
- Result cards show "Hours unknown" — **nothing is enriched yet**, all 3003 places
  lack hours
- Hard-reload (Ctrl+Shift+R) if `app.js` looks stale

---

## 2. Retune defaults for the new density

The corpus went from 967 to 3003 places, **3.1x**, but every default was tuned for
the old density. Expect the UI to feel flooded.

Look at, in `viz/app.js`:

- **`state.cutoff = 20`** — a 20-minute walk now returns several hundred places. A
  30-minute *bike* radius reaches 2600+, which is most of the corpus. Almost
  certainly too high now.
- **`HEAT_MAX_REVIEWS = 300` / `HEAT_MIN_REVIEWS = 15`** — the "heating up" badge
  thresholds. With 3x the corpus these may fire on far more places than intended.
- **Hidden gems bottom-quartile cut** — the p25 review ceiling moved from 154 to
  113 reviews, so the gem set is a different shape. 417 candidates now.
- **Marker density on the map** — `renderLayers` draws every survivor. Several
  hundred dots may need a cap, or size/opacity scaling by rank.
- **Results list length** — check `renderList` doesn't try to render 900 cards.

Measure before changing: drop a pin, read the count in the status pill, decide
whether that number is useful to a person.

---

## 3. Round 2 of the saturation fix — do not start until 1 and 2 are done

**54 of 120 queries hit the 60-result ceiling** in the last pull. Those cells lost
their long tail, which is exactly what "Hidden gems" is built from.

Agreed approach, already settled with the user:

- **Stopping condition: zero cells returning exactly 60 on any type.** NOT "estimated
  counts under 30" — that estimate came from already-truncated data and inherits the
  bias it is meant to correct. The cells it judged *sparsest* capped hardest: 7 of 8
  at the 2263 m tier.
- **Use `rankby=distance`.** Verified live against the API:
  - mutually exclusive with `radius` — sending both returns `INVALID_REQUEST`
  - does **not** lift the 60 cap; you get the 60 *nearest* rather than the 60 *most
    prominent*
  - yields a **provable coverage radius**: "the 60th result is at X metres" means you
    have every restaurant within X metres. Prominence ranking gives no such guarantee.
- **Persist per-cell farthest-returned distance into `places.json` meta** as a
  coverage radius, so completeness is a shipped, inspectable property rather than a
  build-time inference.
- **Report where coverage is thinnest** once it lands.

A draft script exists in the session scratchpad (`resubdivide.py`) but was written for
the old ≤30 criterion and needs rewriting to the cap-hit rule.

**Do not pull without asking. A pull costs ~$11.52.**

---

## Open decisions the user has not made

1. **Île-des-Sœurs walk network.** `retain_all=False` in `build_networks.py` keeps
   only the largest connected component; the island's footpaths reach the mainland
   only over bridges, so osmnx discarded it. ~30 island places are unreachable on
   foot. Bike works. Fix is `retain_all=True` (free rebuild, ~5 min) at the cost of
   pulling in every disconnected fragment in the bbox.
2. **Enrichment budget.** `--limit 200` covers 200 of 417 gem candidates (48%). All
   417 would be ~$9.17. Currently **nothing is enriched** — all 3003 places lack hours.
3. **UI category gap.** The UI offers Bakery, Bar, Takeout and Grocery filters, but
   `PLACE_TYPES = ["restaurant", "cafe"]`. Those four are thin by construction.
   Pre-existing; the user wants it treated as a separate decision with its own budget.
4. **`add_hours.py` / `add_recency.py`** are deprecated and superseded by
   `enrich_places.py`. Deletion was requested then deferred. **Ask before deleting.**
5. **The Google Maps logo asset.** The footer currently uses the permitted text form.
   The user said they would download the official asset and drop it in `viz/`. When
   they do, swap the one span marked `GM-MARK` in `index.html` — the CSS already
   implements the required sizing and clear space.
6. **API key rotation.** The key was pasted into a chat transcript. Audited clean —
   never committed, lives only in a gitignored `.env`. The user deferred rotation
   until after enrichment. It still needs doing, along with Cloud Console API and IP
   restrictions.

---

## Ground rules

- **Never run a paid script without explicit approval.** Show the cost prompt first.
  Spent so far: $11.52 pull + $0.064 in API tests.
- **`python prep/reannotate_places.py --check` is the gate.** Free, ~1 second, no
  network. Run it before and after anything that touches `viz/data/`. `deploy.yml`
  runs it before publishing.
- **Back up `prep/data/raw_responses.jsonl`** (8.4 MB) after every pull. It is
  gitignored and is the only artefact that cost money and cannot be regenerated
  locally. Rebuilding `places.json` from it is free.
- **`build_networks.py` needs `/h/annaconda/python.exe`** — `pip` and `python` resolve
  to different interpreters on this machine. Everything else in `prep/` is stdlib-only.
- **`prep/test.py` is the user's scratch file. Leave it untracked.**
- See `KNOWN_ISSUES.md` for the convex-hull isochrone, and `DATA.md` for licensing.
