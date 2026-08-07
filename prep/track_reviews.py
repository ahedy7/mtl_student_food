"""
track_reviews.py  –  run weekly (via GitHub Actions)
Snapshots current user_ratings_total for every place in places.json
and appends {date, count} to viz/data/review_history.json.
Idempotent: if today's entry already exists for a place, skips it.

Usage:
    export GOOGLE_PLACES_API_KEY=your_key_here
    python prep/track_reviews.py
"""

import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import requests

API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")
if not API_KEY:
    sys.exit("Set GOOGLE_PLACES_API_KEY env var before running.")

DETAILS_URL  = "https://maps.googleapis.com/maps/api/place/details/json"
DATA_DIR     = Path(__file__).parent.parent / "viz" / "data"
PLACES_PATH  = DATA_DIR / "places.json"
HISTORY_PATH = DATA_DIR / "review_history.json"
DELAY_S      = 0.06    # stay well under 50 QPS
SAVE_EVERY   = 100


def fetch_review_count(place_id: str) -> "int | None":
    resp = requests.get(DETAILS_URL, params={
        "key":      API_KEY,
        "place_id": place_id,
        "fields":   "user_ratings_total",
    }, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "OK":
        return None
    return data.get("result", {}).get("user_ratings_total")


def main():
    today = date.today().isoformat()
    places = json.loads(PLACES_PATH.read_text())["places"]
    history = json.loads(HISTORY_PATH.read_text()) if HISTORY_PATH.exists() else {}

    todo = [p for p in places if not (history.get(p["id"]) and history[p["id"]][-1]["date"] == today)]
    if not todo:
        print(f"All {len(places)} places already recorded for {today}. Nothing to do.")
        return

    print(f"Snapshotting {len(todo)}/{len(places)} places for {today} …")
    updated = errors = 0

    for i, p in enumerate(todo):
        pid = p["id"]
        hist = history.setdefault(pid, [])
        try:
            count = fetch_review_count(pid)
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
    print(f"\nDone. {updated} places updated, {errors} errors.")


if __name__ == "__main__":
    main()
