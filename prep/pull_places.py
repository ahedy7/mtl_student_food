"""
pull_places.py  –  run once
Fetches restaurants & cafes from Google Places API across central Montreal
and writes viz/data/places.json.

Cost: ~$0.032/call × up to 360 Nearby Search calls ≈ $11.52 max.
Prints the estimate and asks for confirmation before fetching.

Usage:
    export GOOGLE_PLACES_API_KEY=your_key_here
    python prep/pull_places.py [--yes]
"""

import argparse
import json
import math
import os
import sys
import time
from datetime import date
from pathlib import Path

import requests

API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")
if not API_KEY:
    sys.exit("Set GOOGLE_PLACES_API_KEY env var before running.")

NEARBY_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
OUT_PATH = Path(__file__).parent.parent / "viz" / "data" / "places.json"

# Search centres, sized by the saturation rule rather than laid out uniformly.
#
# Nearby Search returns at most 60 results per (centre, type) — 3 pages of 20 —
# ranked by prominence. A centre covering more than 60 restaurants therefore
# drops the least prominent ones silently, which is precisely the long tail the
# "Hidden gems" view is built from. So cells are subdivided until no cell holds
# more than ~30 known places of either type, leaving headroom under the ceiling.
#
# Produced by recursive quadtree subdivision: start at 3200 m squares, split any
# square over the threshold, floor at 400 m. Radius is each square's half-diagonal,
# so the circles cover their squares with no gaps. Empty squares are dropped,
# which removes the river, Mount Royal, and the rail yards automatically.
#
# Counts in the comments are from the previous (truncated) pull, so they are
# lower bounds. Re-run the subdivision against real counts after this pull.
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
DELAY_S = 2.1   # API requires >2 s between page_token requests


def fetch_nearby(lat: float, lon: float, radius_m: int, place_type: str) -> "list[dict]":
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
        # These will be filled by build_networks.py
        "walk_node":   None,
        "bike_node":   None,
        "walk_snap_m": None,
        "bike_snap_m": None,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    max_calls = len(SEARCH_CENTERS) * len(PLACE_TYPES) * 3   # 3 pages max
    est = max_calls * 0.032
    print(f"Centres: {len(SEARCH_CENTERS)}  ({len(PLACE_TYPES)} types x 3 pages each)")
    print(f"Estimated cost: up to ~${est:.2f}  ({max_calls} calls max x $0.032 Nearby Search)")
    if not args.yes:
        answer = input("Proceed? [y/N] ").strip().lower()
        if answer != "y":
            sys.exit("Aborted.")

    seen = {}  # type: dict[str, dict]
    total_requests = 0

    capped = 0
    for i, (lat, lon, radius_m) in enumerate(SEARCH_CENTERS):
        for ptype in PLACE_TYPES:
            print(f"[{i+1}/{len(SEARCH_CENTERS)}] ({lat:.4f},{lon:.4f}) r={radius_m}m "
                  f"type={ptype} …", flush=True)
            raws = fetch_nearby(lat, lon, radius_m, ptype)
            total_requests += 1
            for r in raws:
                p = extract(r)
                if p and p["id"] not in seen:
                    seen[p["id"]] = p
            # 60 means the API ceiling was hit and the long tail was dropped;
            # that cell needs subdividing before the data can be trusted.
            hit_cap = len(raws) >= 60
            capped += hit_cap
            print(f"  +{len(raws)} results, {len(seen)} unique so far"
                  + ("   << HIT 60 CAP - subdivide this cell" if hit_cap else ""))
            time.sleep(0.3)

    places = list(seen.values())
    mean_rating = round(sum(p["rating"] for p in places) / len(places), 4) if places else 0.0

    payload = {
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

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    print(f"\nWrote {len(places)} places → {OUT_PATH}")
    print(f"Mean rating: {mean_rating}")

    # Free velocity snapshot — review counts are already in the Nearby Search response,
    # so we snapshot them here at no extra cost.
    _snapshot_review_history(places, OUT_PATH.parent)


def _snapshot_review_history(places: list, data_dir: Path) -> None:
    """Append today's review counts to review_history.json (no API calls)."""
    history_path = data_dir / "review_history.json"
    history = json.loads(history_path.read_text()) if history_path.exists() else {}
    today = date.today().isoformat()
    added = 0
    for p in places:
        hist = history.setdefault(p["id"], [])
        if not hist or hist[-1]["date"] != today:
            hist.append({"date": today, "count": p["reviews"]})
            added += 1
    history_path.write_text(json.dumps(history, separators=(",", ":")))
    print(f"Snapshotted {added} review counts → {history_path}")


if __name__ == "__main__":
    main()
