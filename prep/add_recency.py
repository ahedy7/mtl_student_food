"""
add_recency.py  –  run after add_hours.py
Enriches viz/data/places.json with a review-recency signal per place.

NOTE: Place Details returns up to 5 reviews sorted by Google's relevance
ranking, not chronologically. recent_share is computed from this small
sample only — it is a rough directional signal, not a full review history.

Cost: ~$0.022/call (Basic + Atmosphere Data tiers).
Prints an estimate and asks for confirmation before making any calls.

Stores per place:
  recent_share   – float 0-1: fraction of sampled reviews from last 6 months,
                   or null if the API returned no reviews
  review_sample  – int: number of reviews the API returned (max 5)

Safe to re-run: skips places that already have recent_share set.

Usage:
    export GOOGLE_PLACES_API_KEY=your_key_here
    python prep/add_recency.py [--yes]
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")
if not API_KEY:
    sys.exit("Set GOOGLE_PLACES_API_KEY env var before running.")

COST_PER_CALL = 0.022        # USD, Basic + Atmosphere Data tiers
DETAILS_URL   = "https://maps.googleapis.com/maps/api/place/details/json"
PLACES_PATH   = Path(__file__).parent.parent / "viz" / "data" / "places.json"
DELAY_S       = 0.06        # 60 ms between requests
SAVE_EVERY    = 50
SIX_MONTHS_S  = 6 * 30 * 24 * 3600   # ~180 days in seconds


def fetch_recency(place_id: str) -> "dict | None":
    resp = requests.get(DETAILS_URL, params={
        "key":      API_KEY,
        "place_id": place_id,
        "fields":   "reviews",
    }, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    if data.get("status") != "OK":
        return None

    reviews = data.get("result", {}).get("reviews", [])
    if not reviews:
        return {"recent_share": None, "review_sample": 0}

    now    = time.time()
    recent = sum(1 for r in reviews if now - r.get("time", 0) < SIX_MONTHS_S)
    return {
        "recent_share":  round(recent / len(reviews), 3),
        "review_sample": len(reviews),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    raw    = json.loads(PLACES_PATH.read_text())
    places = raw["places"]

    todo = [p for p in places if "recent_share" not in p]
    print(f"{len(todo)} places need recency data  ({len(places) - len(todo)} already done)")

    if not todo:
        return

    est = len(todo) * COST_PER_CALL
    print(f"Estimated cost: ~${est:.2f}  ({len(todo)} calls × ${COST_PER_CALL})")
    if not args.yes:
        answer = input("Proceed? [y/N] ").strip().lower()
        if answer != "y":
            sys.exit("Aborted.")

    errors = 0
    for i, place in enumerate(todo):
        try:
            result = fetch_recency(place["id"])
            if result:
                place["recent_share"]  = result["recent_share"]
                place["review_sample"] = result["review_sample"]
            else:
                place["recent_share"]  = None
                place["review_sample"] = 0
        except Exception as e:
            print(f"  x {place['name']}: {e}")
            place["recent_share"]  = None
            place["review_sample"] = 0
            errors += 1

        if (i + 1) % SAVE_EVERY == 0:
            PLACES_PATH.write_text(json.dumps(raw, ensure_ascii=False, separators=(",", ":")))
            pct = round((i + 1) / len(todo) * 100)
            print(f"  {i+1}/{len(todo)}  ({pct}%)  –  saved")

        time.sleep(DELAY_S)

    PLACES_PATH.write_text(json.dumps(raw, ensure_ascii=False, separators=(",", ":")))

    has = sum(1 for p in places if p.get("recent_share") is not None)
    print(f"\nDone. {has}/{len(places)} places have recency data. Errors: {errors}")


if __name__ == "__main__":
    main()
