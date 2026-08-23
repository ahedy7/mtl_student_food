"""
pull_places.py  –  run once (or with --round2 for dense-core subdivision)
Fetches restaurants & cafes from Google Places API across central Montreal
and writes viz/data/places.json.

Cost: ~$0.032/call × up to 360 Nearby Search calls ≈ $11.52 max.
Prints the estimate and asks for confirmation before fetching.

Usage:
    export GOOGLE_PLACES_API_KEY=your_key_here
    python prep/pull_places.py [--yes]
    python prep/pull_places.py --round2 [--yes]   # dense-core subdivision
    python prep/pull_places.py --round2 --dry-run  # preview queries and cost, no API calls
"""

import argparse
import json
import math
import os
import sys
import time
from datetime import date
from pathlib import Path

from jsonio import ensure_utf8_stdout, preflight, read_json, write_json
from rawbank import RawBank

import requests

API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")
# Key is checked at run-time; --dry-run does not need it.

NEARBY_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
OUT_PATH = Path(__file__).parent.parent / "viz" / "data" / "places.json"

# Search centres, sized by the saturation rule rather than laid out uniformly.
#
# Nearby Search returns at most 60 results per (centre, type) — 3 pages of 20 —
# ranked by prominence. A centre covering more than 60 restaurants therefore
# drops the least prominent ones silently, which is precisely the long tail the
# "Hidden gems" view is built from.
#
# Produced by recursive quadtree subdivision: start at 3200 m squares, split any
# square over the threshold, floor at 400 m. Radius is each square's half-diagonal,
# so the circles cover their squares with no gaps. Empty squares are dropped,
# which removes the river, Mount Royal, and the rail yards automatically.
#
# SIZING CRITERION: subdivide until no query returns exactly 60, i.e. until no cell
# hits the ceiling. The earlier criterion — keep estimated counts under ~30 — was
# derived from an already-truncated pull, so it inherited the very bias it was
# meant to correct, and the cells it judged sparse turned out to cap the hardest.
# Cap hits are direct evidence and carry no such bias.
#
# The counts in the comments below are from that truncated pull and are lower
# bounds only. This layout is round 1; re-run the subdivision against cap hits.
#
#   (lat, lon, radius_m)
SEARCH_CENTERS = [
    # ---- radius 1131 m ----
    (45.55280, -73.60873, 1131),  # 14r/13c known
    (45.55280, -73.58823, 1131),  # 10r/2c known
    # ---- radius 2263 m ----
    (45.54561, -73.63949, 2263),  # 7r/4c known
    (45.54561, -73.55747, 2263),  # 27r/12c known
    # ---- radius 566 m ----
    (45.54202, -73.61386, 566),  # 14r/15c known
    (45.54202, -73.60361, 566),  # 11r/10c known
    # ---- radius 1131 m ----
    (45.53842, -73.58823, 1131),  # 17r/20c known
    # ---- radius 566 m ----
    (45.53483, -73.61386, 566),  # 21r/5c known
    (45.53483, -73.60361, 566),  # 23r/17c known
    (45.52764, -73.61386, 566),  # 3r/3c known
    (45.52764, -73.60361, 566),  # 8r/10c known
    (45.52764, -73.59335, 566),  # 12r/13c known
    (45.52764, -73.58310, 566),  # 14r/16c known
    # ---- radius 1131 m ----
    (45.52405, -73.56772, 1131),  # 29r/8c known
    (45.52405, -73.54722, 1131),  # 26r/9c known
    # ---- radius 283 m ----
    (45.52225, -73.59592, 283),  # 11r/6c known
    (45.52225, -73.59079, 283),  # 5r/3c known
    # ---- radius 566 m ----
    (45.52046, -73.61386, 566),  # 26r/11c known
    (45.52046, -73.60361, 566),  # 18r/12c known
    (45.52046, -73.58310, 566),  # 21r/22c known
    # ---- radius 283 m ----
    (45.51866, -73.59592, 283),  # 13r/4c known
    (45.51866, -73.59079, 283),  # 3r/3c known
    # ---- radius 2263 m ----
    (45.51686, -73.63949, 2263),  # 1r/1c known
    (45.51686, -73.51646, 2263),  # 3r/0c known
    # ---- radius 566 m ----
    (45.51327, -73.57285, 566),  # 17r/9c known
    (45.51327, -73.56260, 566),  # 13r/8c known
    # ---- radius 1131 m ----
    (45.50968, -73.60873, 1131),  # 2r/1c known
    (45.50968, -73.58823, 1131),  # 7r/8c known
    (45.50968, -73.54722, 1131),  # 27r/22c known
    # ---- radius 283 m ----
    (45.50788, -73.57541, 283),  # 5r/6c known
    (45.50788, -73.57029, 283),  # 9r/5c known
    # ---- radius 566 m ----
    (45.50609, -73.56260, 566),  # 24r/17c known
    # ---- radius 283 m ----
    (45.50429, -73.57541, 283),  # 6r/7c known
    (45.50429, -73.57029, 283),  # 15r/12c known
    (45.50070, -73.57541, 283),  # 28r/15c known
    (45.50070, -73.57029, 283),  # 15r/6c known
    # ---- radius 566 m ----
    (45.49890, -73.56260, 566),  # 19r/14c known
    # ---- radius 283 m ----
    (45.49710, -73.57541, 283),  # 24r/5c known
    (45.49710, -73.57029, 283),  # 5r/4c known
    # ---- radius 1131 m ----
    (45.49531, -73.62924, 1131),  # 9r/5c known
    (45.49531, -73.60873, 1131),  # partial data (Westmount)
    (45.49531, -73.58823, 1131),  # partial data (Westmount)
    (45.49531, -73.54722, 1131),  # 11r/3c known
    # ---- radius 566 m ----
    (45.49171, -73.57285, 566),  # 4r/0c known
    (45.49171, -73.56260, 566),  # 11r/9c known
    (45.48453, -73.63437, 566),  # 16r/10c known
    (45.48453, -73.62411, 566),  # 25r/7c known
    (45.48453, -73.59335, 566),  # partial data (Westmount)
    (45.48453, -73.58310, 566),  # partial data (Westmount)
    # ---- radius 1131 m ----
    (45.48093, -73.60873, 1131),  # UNKNOWN density (Westmount)
    (45.48093, -73.56772, 1131),  # 23r/23c known
    # ---- radius 566 m ----
    (45.47734, -73.63437, 566),  # partial data (Monkland / NDG west)
    (45.47734, -73.62411, 566),  # partial data (Monkland / NDG west)
    (45.47734, -73.59335, 566),  # partial data (Verdun / Wellington)
    (45.47734, -73.58310, 566),  # partial data (Verdun / Wellington)
    # ---- radius 1131 m ----
    (45.46656, -73.56772, 1131),  # UNKNOWN density (Verdun / Wellington)
    (45.46656, -73.54722, 1131),  # UNKNOWN density (Verdun / Wellington)
    # ---- radius 2263 m ----
    (45.45937, -73.59848, 2263),  # partial data (Verdun / Wellington)
    # ---- radius 1131 m ----
    (45.45219, -73.56772, 1131),  # UNKNOWN density (Verdun / Wellington)
    (45.45219, -73.54722, 1131),  # UNKNOWN density (Verdun / Wellington)
]

PLACE_TYPES = ["restaurant", "cafe"]
CHECKPOINT_EVERY = 5   # centres between atomic saves
DELAY_S = 2.1   # API requires >2 s between page_token requests

# ── Round-2 centres (dense core only) ────────────────────────────────────────
#
# radius=0 is the bank-key sentinel for rankby=distance queries. These are never
# mixed with radius queries, so there is no key collision with round-1 entries.
#
# 4 r=566m parents subdivided into 16 child centres at r=283m.
# Parents: (45.50609,-73.56260), (45.52764,-73.60361),
#          (45.52046,-73.58310), (45.52764,-73.59335)
# All within 523m of the Plateau/Mile End core; all had both types capped.
ROUND2_CENTERS = [
    # ---- A: child centres of r=566m parents (each nominally covers r=283m) ----
    (45.50863, -73.55897, 0),
    (45.50863, -73.56623, 0),
    (45.50355, -73.55897, 0),
    (45.50355, -73.56623, 0),
    (45.53018, -73.59998, 0),
    (45.53018, -73.60724, 0),
    (45.52510, -73.59998, 0),
    (45.52510, -73.60724, 0),
    (45.52300, -73.57947, 0),
    (45.52300, -73.58673, 0),
    (45.51792, -73.57947, 0),
    (45.51792, -73.58673, 0),
    (45.53018, -73.58972, 0),
    (45.53018, -73.59698, 0),
    (45.52510, -73.58972, 0),
    (45.52510, -73.59698, 0),
]


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Straight-line distance in metres between two lat/lon points."""
    R = 6_371_000
    d = math.radians
    a = (math.sin(d(lat2 - lat1) / 2) ** 2
         + math.cos(d(lat1)) * math.cos(d(lat2)) * math.sin(d(lon2 - lon1) / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def fetch_nearby(lat: float, lon: float, radius_m: int, place_type: str,
                 bank: "RawBank | None" = None,
                 rankby_distance: bool = False) -> "list[dict]":
    """
    Fetch one (centre, type) query, up to 3 pages.

    Every response is banked verbatim before anything parses it, so the paid
    bytes survive any later failure and places.json can be rebuilt offline.

    rankby_distance=True uses rankby=distance instead of a fixed radius. The
    centre tuple uses radius=0 as a bank-key sentinel so resume logic is
    unchanged — a (lat, lon, 0, type) key cannot collide with any round-1 entry.
    radius and rankby=distance are mutually exclusive in the API.
    """
    centre = (lat, lon, radius_m)   # radius_m=0 for distance-ranked queries
    if rankby_distance:
        params = {
            "key": API_KEY,
            "location": f"{lat},{lon}",
            "rankby": "distance",
            "type": place_type,
        }
    else:
        params = {
            "key": API_KEY,
            "location": f"{lat},{lon}",
            "radius": radius_m,
            "type": place_type,
        }
    results = []
    page = 0
    while True:
        resp = requests.get(NEARBY_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        # Bank first. Parsing comes after; if it throws, the bytes are already safe.
        if bank is not None:
            bank.record_response(centre, place_type, page, data)

        status = data.get("status")
        if status not in ("OK", "ZERO_RESULTS"):
            print(f"  API status: {status}")
            break
        results.extend(data.get("results", []))
        token = data.get("next_page_token")
        if not token or page >= 2:
            break
        time.sleep(DELAY_S)
        params = {"key": API_KEY, "pagetoken": token}
        page += 1

    # Only now is the query complete; a partial query must be re-fetched on resume.
    if bank is not None:
        bank.record_query_complete(centre, place_type, page + 1, len(results))
    return results


def extract(raw: dict) -> "dict | None":
    place_id = raw.get("place_id")
    if not place_id:
        return None
    geo = raw.get("geometry", {}).get("location", {})
    lat, lon = geo.get("lat"), geo.get("lng")
    if lat is None or lon is None:
        return None
    rating = raw.get("rating")
    reviews = raw.get("user_ratings_total", 0)
    price = raw.get("price_level")         # 1-4 or None
    types = raw.get("types", [])
    name = raw.get("name", "")
    # Drop places without enough signal
    if rating is None or reviews < 5:
        return None
    # Google requires attributions to be displayed verbatim when present. Nearby
    # Search returns the field empty in practice, but carry it through rather than
    # discover later that we dropped something we were obliged to show.
    #
    # Deliberately NOT collecting photos[].html_attributions. Those are photographer
    # credits, and the obligation to show them attaches to displaying the photo.
    # This app displays no photos, so surfacing the credits would attribute images
    # that are not shown — and would store 3000 individuals' names for nothing.
    # If photos are ever displayed, collect and render them here.
    attributions = [a for a in (raw.get("html_attributions") or []) if a]

    return {
        "id": place_id,
        "name": name,
        "lat": round(lat, 6),
        "lon": round(lon, 6),
        "rating": rating,
        "reviews": reviews,
        "price": price,          # may be None
        "types": [t for t in types if not t.startswith("point_of_interest")
                  and t not in ("establishment", "food")],
        **({"attributions": sorted(set(attributions))} if attributions else {}),
        # These will be filled by build_networks.py
        "walk_node":   None,
        "bike_node":   None,
        "walk_snap_m": None,
        "bike_snap_m": None,
    }


def _key(centre, place_type):
    """Resume key for a (centre, type) query. Must match rawbank._key."""
    lat, lon, radius = centre
    return (round(lat, 5), round(lon, 5), int(radius), place_type)


def _rebuild_from_bank(bank, coverage: "dict | None" = None) -> "tuple[list, dict]":
    """
    Rebuild places.json from banked raw responses. Pure local transform, no API.

    This is the only path that writes places.json, so a run that fetches nothing
    new produces exactly the same output as one that fetched everything — and
    changing how places are parsed or filtered costs nothing to re-apply.

    coverage: optional dict of {"lat,lon": radius_m} for round-2 cells, persisted
    into meta so completeness is inspectable in the shipped data.
    """
    seen = {}
    n_resp = 0
    response_attributions = set()
    for rec in bank.responses():
        n_resp += 1
        resp = rec.get("response", {})
        # Response-level attributions apply to the result set as a whole.
        response_attributions.update(a for a in (resp.get("html_attributions") or []) if a)
        for raw in resp.get("results", []):
            p = extract(raw)
            if p and p["id"] not in seen:
                seen[p["id"]] = p

    payload = _build_payload(seen)
    if response_attributions:
        payload["meta"]["attributions"] = sorted(response_attributions)
    if coverage:
        payload["meta"]["coverage_radius_m"] = coverage
    write_json(OUT_PATH, payload)
    print(f"  rebuilt from {n_resp} banked responses -> {len(seen)} unique places")
    return list(seen.values()), payload


def _build_payload(seen: dict) -> dict:
    """Assemble the places.json payload from whatever has been gathered so far."""
    places = list(seen.values())
    mean_rating = (round(sum(p["rating"] for p in places) / len(places), 4)
                   if places else 0.0)
    return {
        "meta": {
            "mean_rating": mean_rating,
            "count": len(places),
            # Surfaced in the UI as "Data updated N days ago" (see renderDataAge
            # in viz/app.js), so a stale deploy is visible without digging.
            "generated": date.today().isoformat(),
            "note": "walk_node and bike_node are filled by build_networks.py",
        },
        "places": places,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    parser.add_argument("--force", action="store_true",
                        help="Re-fetch every centre, ignoring what is already banked")
    parser.add_argument("--round2", action="store_true",
                        help="Pull dense-core subdivision centres with rankby=distance")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print query list and cost estimate; make no API calls")
    args = parser.parse_args()

    if not args.dry_run and not API_KEY:
        sys.exit("Set GOOGLE_PLACES_API_KEY env var before running.")

    ensure_utf8_stdout()
    bank = RawBank()

    centers = ROUND2_CENTERS if args.round2 else SEARCH_CENTERS
    rankby_distance = args.round2

    # Resume: anything already banked has been paid for once and is not re-fetched.
    done = set() if args.force else bank.completed()
    todo_queries = [(c, t) for c in centers for t in PLACE_TYPES
                    if args.force or _key(c, t) not in done]
    skipped = len(centers) * len(PLACE_TYPES) - len(todo_queries)

    max_calls = len(todo_queries) * 3   # 3 pages max per query
    est = max_calls * 0.032
    st = bank.stats()
    mode_label = "rankby=distance (round 2)" if args.round2 else "radius (round 1)"
    print(f"Mode           : {mode_label}")
    print(f"Centres        : {len(centers)}  ({len(PLACE_TYPES)} types x 3 pages each)")
    if st["responses"]:
        print(f"Already banked : {st['queries_complete']} complete queries, "
              f"{st['responses']} responses, {st['bytes']/1024:.0f} KB")
    if args.force and skipped == 0 and st["queries_complete"]:
        print(f"--force: re-fetching all {st['queries_complete']} banked queries")
    print(f"Will skip      : {skipped} query(ies) already banked  (cost $0.00)")
    print(f"Will fetch     : {len(todo_queries)} query(ies)")
    print(f"Estimated cost : up to ~${est:.2f}  ({max_calls} calls max x $0.032 Nearby Search)")

    if args.dry_run:
        print(f"\n--- DRY RUN: query list ({len(todo_queries)} queries) ---")
        for i, ((lat, lon, r), ptype) in enumerate(todo_queries, 1):
            label = "rankby=distance" if args.round2 else f"r={r}m"
            print(f"  [{i:>3}] ({lat:.5f}, {lon:.5f})  {label}  type={ptype}")
        print(f"\n--- No API calls made. Remove --dry-run to proceed. ---")
        return

    if not todo_queries:
        print("\nNothing to fetch. Rebuilding places.json from the bank (free) ...")
        _rebuild_from_bank(bank)
        return

    if not args.yes:
        answer = input("Proceed? [y/N] ").strip().lower()
        if answer != "y":
            sys.exit("Aborted.")

    # Prove we can write non-cp1252 text here BEFORE spending anything. A full
    # 60-centre pull was once lost to a UnicodeEncodeError raised by the final
    # write — 3003 places gathered, then destroyed by the save. One second here
    # beats discovering it after the last API call.
    try:
        preflight(OUT_PATH)
    except Exception as exc:
        sys.exit(f"Pre-flight write test failed, refusing to spend: {exc}")
    print("Pre-flight write test: OK")

    # Load known place IDs so we can report net-new per query.
    known_ids = set()
    if OUT_PATH.exists():
        try:
            known_ids = {p["id"] for p in read_json(OUT_PATH)["places"]}
        except Exception:
            pass

    total_requests = 0
    capped = 0
    still_capped = 0
    coverage = {}   # "lat,lon" → farthest result distance in metres

    with bank:
        for n, ((lat, lon, radius_m), ptype) in enumerate(todo_queries, 1):
            label = "rankby=distance" if rankby_distance else f"r={radius_m}m"
            print(f"[{n}/{len(todo_queries)}] ({lat:.5f},{lon:.5f})  {label}  type={ptype} …",
                  flush=True)
            raws = fetch_nearby(lat, lon, radius_m, ptype, bank=bank,
                                rankby_distance=rankby_distance)
            total_requests += 1

            # Per-query stats
            hit_cap = len(raws) >= 60
            capped += hit_cap
            if hit_cap:
                still_capped += 1

            net_new = sum(1 for r in raws if r.get("place_id") not in known_ids)

            # Coverage radius: haversine from centre to farthest returned result
            far_m = 0.0
            for r in raws:
                geo = r.get("geometry", {}).get("location", {})
                rlat, rlon = geo.get("lat"), geo.get("lng")
                if rlat is not None and rlon is not None:
                    far_m = max(far_m, _haversine_m(lat, lon, rlat, rlon))
            cov_key = f"{lat:.5f},{lon:.5f}"
            coverage[cov_key] = round(far_m)

            cap_flag = "  << STILL CAPPED" if hit_cap else ""
            print(f"  +{len(raws)} results  |  {net_new} net-new  |  farthest {far_m:.0f}m{cap_flag}")
            time.sleep(0.3)

    # places.json is a pure local transform over the bank, so build it from there
    # rather than from whatever this run happened to hold in memory.
    places, payload = _rebuild_from_bank(bank, coverage=coverage if args.round2 else None)
    mean_rating = payload["meta"]["mean_rating"]
    print(f"\nWrote {len(places)} places → {OUT_PATH}")
    print(f"Mean rating: {mean_rating}")

    # Free velocity snapshot — review counts are already in the Nearby Search response,
    # so we snapshot them here at no extra cost.
    _snapshot_review_history(places, OUT_PATH.parent)

    if still_capped:
        print(f"\nWARNING: {still_capped} round-2 query(ies) still hit the 60-result ceiling. "
              f"Those cells need further subdivision.")
    elif capped == 0 and args.round2:
        print(f"\nAll round-2 cells returned fewer than 60 — dense core is provably exhausted.")

    # The bank is the only artefact here that cost money and cannot be rebuilt
    # locally. It is gitignored deliberately (raw Google content, and an
    # append-only file is a poor fit for git), so back it up out of band.
    st = bank.stats()
    print(f"\nRaw response bank: {bank.path}")
    print(f"  {st['responses']} responses, {st['queries_complete']} complete queries, "
          f"{st['bytes']/1024/1024:.1f} MB")
    print(f"  NOT in git. Copy it somewhere durable now — re-fetching costs money, "
          f"rebuilding from it is free. See 'Backing up the raw bank' in README.md.")


def _snapshot_review_history(places: list, data_dir: Path) -> None:
    """Append today's review counts to review_history.json (no API calls)."""
    history_path = data_dir / "review_history.json"
    history = read_json(history_path) if history_path.exists() else {}
    today = date.today().isoformat()
    added = 0
    for p in places:
        hist = history.setdefault(p["id"], [])
        if not hist or hist[-1]["date"] != today:
            hist.append({"date": today, "count": p["reviews"]})
            added += 1
    write_json(history_path, history)
    print(f"Snapshotted {added} review counts → {history_path}")


if __name__ == "__main__":
    main()
