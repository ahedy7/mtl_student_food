"""
track_reviews.py  –  optional manual tool
Snapshots review counts into viz/data/review_history.json.

Default (FREE): reads current counts from places.json — no API calls.
Run this after pull_places.py to record another data point.
Note: pull_places.py already calls this automatically, so you usually
don't need to run this manually.

Paid mode (--api): fetches fresh user_ratings_total via Places Details API.
Cost: ~$0.017/call. Use --limit N to cap spend.

Usage:
    python prep/track_reviews.py                  # free, reads places.json
    python prep/track_reviews.py --api --limit 50 # paid, max 50 calls
    python prep/track_reviews.py --dry-run        # show what would run, no writes
"""

import argparse
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import requests

COST_PER_CALL = 0.017   # USD, Places Details Basic Data tier
DETAILS_URL   = "https://maps.googleapis.com/maps/api/place/details/json"
DATA_DIR      = Path(__file__).parent.parent / "viz" / "data"
PLACES_PATH   = DATA_DIR / "places.json"
HISTORY_PATH  = DATA_DIR / "review_history.json"
DELAY_S       = 0.06
SAVE_EVERY    = 100


def fetch_review_count(place_id: str, api_key: str) -> "int | None":
    resp = requests.get(DETAILS_URL, params={
        "key":      api_key,
        "place_id": place_id,
        "fields":   "user_ratings_total",
    }, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "OK":
        return None
    return data.get("result", {}).get("user_ratings_total")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", action="store_true",
                        help="Fetch fresh counts from Places API (costs money)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max API calls (--api mode only)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would run without writing anything")
    args = parser.parse_args()

    today   = date.today().isoformat()
    places  = json.loads(PLACES_PATH.read_text())["places"]
    history = json.loads(HISTORY_PATH.read_text()) if HISTORY_PATH.exists() else {}

    def last_date(p):
        h = history.get(p["id"])
        return h[-1]["date"] if h else "0000-00-00"

    todo = sorted(
        [p for p in places if last_date(p) != today],
        key=last_date,
    )

    if not todo:
        print(f"All {len(places)} places already recorded for {today}.")
        return

    if args.api:
        api_key = os.environ.get("GOOGLE_PLACES_API_KEY", "")
        if not api_key:
            sys.exit("Set GOOGLE_PLACES_API_KEY env var to use --api mode.")
        if args.limit:
            todo = todo[: args.limit]
        est = len(todo) * COST_PER_CALL
        print(f"[PAID] {len(todo)} API calls, estimated cost ~${est:.2f}")
        if args.dry_run:
            print("[dry-run] No calls made.")
            return
        updated = errors = 0
        for i, p in enumerate(todo):
            hist = history.setdefault(p["id"], [])
            try:
                count = fetch_review_count(p["id"], api_key)
                if count is not None:
                    hist.append({"date": today, "count": count})
                    updated += 1
            except Exception as e:
                print(f"  x {p['name']}: {e}")
                errors += 1
            if (i + 1) % SAVE_EVERY == 0:
                HISTORY_PATH.write_text(json.dumps(history, separators=(",", ":")))
                print(f"  {i+1}/{len(todo)} saved")
            time.sleep(DELAY_S)
        HISTORY_PATH.write_text(json.dumps(history, separators=(",", ":")))
        print(f"Done. {updated} updated, {errors} errors. ~${updated * COST_PER_CALL:.2f} spent")
    else:
        print(f"[free] Snapshotting {len(todo)} places from places.json (no API calls)")
        if args.dry_run:
            print("[dry-run] No writes.")
            return
        for p in todo:
            history.setdefault(p["id"], []).append({"date": today, "count": p["reviews"]})
        HISTORY_PATH.write_text(json.dumps(history, separators=(",", ":")))
        print(f"Done. {len(todo)} places snapshotted.")


if __name__ == "__main__":
    main()
