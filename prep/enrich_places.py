"""
enrich_places.py  –  run after pull_places.py (replaces add_hours.py + add_recency.py)
Fetches opening_hours AND reviews in ONE Details call per place, so you pay once
instead of twice. Supersedes add_hours.py and add_recency.py.

Cost: ~$0.022/call (Basic + Contact + Atmosphere tiers combined).
Default --limit 200 keeps total spend at ~$4.40, so the full pipeline
(pull_places + enrich) stays under $7.50.

Places are prioritised by review count (most-reviewed first) so the limit
always covers the places most likely to appear in search results.

Safe to re-run: skips places that already have BOTH hours and recent_share set.

Usage:
    export GOOGLE_PLACES_API_KEY=your_key_here
    python prep/enrich_places.py [--limit N] [--yes] [--dry-run]
"""

import argparse
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import requests

COST_PER_CALL = 0.022   # USD: Basic + Contact + Atmosphere tiers
DETAILS_URL   = "https://maps.googleapis.com/maps/api/place/details/json"
PLACES_PATH   = Path(__file__).parent.parent / "viz" / "data" / "places.json"
SIX_MONTHS_S  = 6 * 30 * 24 * 3600
DELAY_S       = 0.06
SAVE_EVERY    = 50


def fetch_details(place_id: str, api_key: str) -> dict:
    resp = requests.get(DETAILS_URL, params={
        "key":      api_key,
        "place_id": place_id,
        "fields":   "opening_hours,reviews",
    }, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "OK":
        return {"hours": None, "recent_share": None, "review_sample": 0}

    result = data.get("result", {})

    # opening hours → compact [[open_day, open_hhmm, close_day, close_hhmm], ...]
    periods = result.get("opening_hours", {}).get("periods", [])
    hours = None
    if periods:
        compact = []
        for p in periods:
            o = p.get("open", {})
            c = p.get("close")
            od, ot = o.get("day", 0), int(o.get("time", "0000"))
            if c is None:
                compact.append([od, ot, -1, -1])   # 24/7
            else:
                compact.append([od, ot, c.get("day", od), int(c.get("time", "2359"))])
        hours = compact or None

    # recency from sampled reviews (up to 5, Google's max)
    reviews = result.get("reviews", [])
    if reviews:
        now    = time.time()
        recent = sum(1 for r in reviews if now - r.get("time", 0) < SIX_MONTHS_S)
        recent_share  = round(recent / len(reviews), 3)
        review_sample = len(reviews)
    else:
        recent_share  = None
        review_sample = 0

    return {"hours": hours, "recent_share": recent_share, "review_sample": review_sample}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=200,
                        help="Max API calls (default: 200, ~$4.40)")
    parser.add_argument("--yes",     action="store_true", help="Skip confirmation prompt")
    parser.add_argument("--dry-run", action="store_true", help="Show plan, no API calls")
    args = parser.parse_args()

    api_key = os.environ.get("GOOGLE_PLACES_API_KEY", "")
    if not api_key:
        sys.exit("Set GOOGLE_PLACES_API_KEY env var before running.")

    raw    = json.loads(PLACES_PATH.read_text())
    places = raw["places"]

    # Skip places that already have both fields; sort remainder by reviews desc
    todo = sorted(
        [p for p in places if "hours" not in p or "recent_share" not in p],
        key=lambda p: p.get("reviews", 0),
        reverse=True,
    )
    todo = todo[: args.limit]

    already = len(places) - len([p for p in places if "hours" not in p or "recent_share" not in p])
    est     = len(todo) * COST_PER_CALL
    print(f"Places to enrich : {len(todo)}  ({already} already done, {len(places)} total)")
    print(f"Estimated cost   : ~${est:.2f}  ({len(todo)} calls × ${COST_PER_CALL})")

    if args.dry_run:
        print("[dry-run] No API calls made.")
        return

    if not args.yes:
        answer = input("Proceed? [y/N] ").strip().lower()
        if answer != "y":
            sys.exit("Aborted.")

    place_map = {p["id"]: p for p in places}
    fetched = errors = 0

    for i, p in enumerate(todo):
        try:
            details = fetch_details(p["id"], api_key)
            place_map[p["id"]].update(details)
            fetched += 1
        except Exception as e:
            print(f"  x {p['name']}: {e}")
            place_map[p["id"]].update({"hours": None, "recent_share": None, "review_sample": 0})
            errors += 1

        if (i + 1) % SAVE_EVERY == 0:
            PLACES_PATH.write_text(json.dumps(raw, ensure_ascii=False, separators=(",", ":")))
            print(f"  {i+1}/{len(todo)}  ({round((i+1)/len(todo)*100)}%)  –  saved")

        time.sleep(DELAY_S)

    # Surfaced in the UI as "Data updated N days ago" (renderDataAge in viz/app.js).
    raw.setdefault("meta", {})["enriched"] = date.today().isoformat()
    PLACES_PATH.write_text(json.dumps(raw, ensure_ascii=False, separators=(",", ":")))
    print(f"\nDone. {fetched} enriched, {errors} errors. Actual cost ~${fetched * COST_PER_CALL:.2f}")


if __name__ == "__main__":
    main()
