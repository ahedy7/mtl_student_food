"""
add_hours.py  –  run after pull_places.py
Enriches viz/data/places.json with opening hours via Places Details API.
Safe to re-run: skips places that already have hours data.

Cost: ~$0.020/call (Basic + Contact Data tiers).
Prints an estimate and asks for confirmation before making any calls.

Hours are stored as compact arrays:
  [[open_day, open_hhmm, close_day, close_hhmm], ...]
  Day 0=Sun … 6=Sat, time is integer HHMM (e.g. 830 = 8:30am, 2200 = 10pm)
  close_day = -1 means open 24/7 (no close period)
  null  = hours unknown / not available

Usage:
    export GOOGLE_PLACES_API_KEY=your_key_here
    python prep/add_hours.py [--yes]
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

COST_PER_CALL = 0.020   # USD, Basic + Contact Data tiers
DETAILS_URL   = "https://maps.googleapis.com/maps/api/place/details/json"
PLACES_PATH   = Path(__file__).parent.parent / "viz" / "data" / "places.json"
DELAY_S       = 0.06   # 60 ms between requests – well under quota limits
SAVE_EVERY    = 50     # write progress to disk every N places


def fetch_hours(place_id: str) -> "list | None":
    resp = requests.get(DETAILS_URL, params={
        "key":      API_KEY,
        "place_id": place_id,
        "fields":   "opening_hours",
    }, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    if data.get("status") != "OK":
        return None

    periods = data.get("result", {}).get("opening_hours", {}).get("periods", [])
    if not periods:
        return None

    compact = []
    for p in periods:
        o = p.get("open", {})
        c = p.get("close")
        od = o.get("day", 0)
        ot = int(o.get("time", "0000"))
        if c is None:
            compact.append([od, ot, -1, -1])   # 24/7
        else:
            compact.append([od, ot, c.get("day", od), int(c.get("time", "2359"))])

    return compact or None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    raw    = json.loads(PLACES_PATH.read_text())
    places = raw["places"]

    todo = [p for p in places if "hours" not in p]
    print(f"{len(todo)} places need hours  ({len(places) - len(todo)} already have data)")

    if not todo:
        return

    est = len(todo) * COST_PER_CALL
    print(f"Estimated cost: ~${est:.2f}  ({len(todo)} calls × ${COST_PER_CALL})")
    if not args.yes:
        answer = input("Proceed? [y/N] ").strip().lower()
        if answer != "y":
            sys.exit("Aborted.")

    fetched = errors = 0
    for i, place in enumerate(todo):
        try:
            place["hours"] = fetch_hours(place["id"])
            fetched += 1
        except Exception as e:
            print(f"  ✗ {place['name']}: {e}")
            place["hours"] = None
            errors += 1

        if (i + 1) % SAVE_EVERY == 0:
            PLACES_PATH.write_text(json.dumps(raw, ensure_ascii=False, separators=(",", ":")))
            pct = round((i + 1) / len(todo) * 100)
            print(f"  {i+1}/{len(todo)}  ({pct}%)  –  saved")

        time.sleep(DELAY_S)

    PLACES_PATH.write_text(json.dumps(raw, ensure_ascii=False, separators=(",", ":")))

    has = sum(1 for p in places if p.get("hours") is not None)
    print(f"\nDone. {has}/{len(places)} places have hours data. Errors: {errors}")


if __name__ == "__main__":
    main()
