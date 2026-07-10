"""
pull_places.py  –  run once
Fetches restaurants & cafes from Google Places API across central Montreal
and writes viz/data/places.json.

Usage:
    export GOOGLE_PLACES_API_KEY=your_key_here
    python prep/pull_places.py
"""

import json
import math
import os
import sys
import time
from pathlib import Path

import requests

API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")
if not API_KEY:
    sys.exit("Set GOOGLE_PLACES_API_KEY env var before running.")

NEARBY_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
OUT_PATH = Path(__file__).parent.parent / "viz" / "data" / "places.json"

# Search centers covering central Montreal; radius 1200 m each
SEARCH_CENTERS = [
    (45.5236, -73.5803),   # Plateau-Mont-Royal
    (45.5282, -73.5985),   # Mile End
    (45.5048, -73.5772),   # Downtown
    (45.5076, -73.5539),   # Old Montreal
    (45.4976, -73.5797),   # Shaughnessy Village / UQAM
    (45.5399, -73.5983),   # Rosemont
    (45.5459, -73.6100),   # Villeray
    (45.4860, -73.6261),   # Notre-Dame-de-Grâce
    (45.4900, -73.5700),   # St-Henri / Griffintown
    (45.5150, -73.6100),   # Côte-des-Neiges
    (45.5030, -73.5620),   # Quartier latin
    (45.5320, -73.5470),   # Hochelaga
    (45.5080, -73.5850),   # Guy-Concordia / Loyola corridor
    (45.4820, -73.5820),   # Verdun
]

PLACE_TYPES = ["restaurant", "cafe"]
RADIUS_M = 1200
DELAY_S = 2.1   # API requires >2 s between page_token requests


def fetch_nearby(lat: float, lon: float, place_type: str) -> "list[dict]":
    params = {
        "key": API_KEY,
        "location": f"{lat},{lon}",
        "radius": RADIUS_M,
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
        "walk_node": None,
        "bike_node": None,
    }


def main():
    seen = {}  # type: dict[str, dict]
    total_requests = 0

    for i, (lat, lon) in enumerate(SEARCH_CENTERS):
        for ptype in PLACE_TYPES:
            print(f"[{i+1}/{len(SEARCH_CENTERS)}] ({lat:.4f},{lon:.4f}) type={ptype} …", flush=True)
            raws = fetch_nearby(lat, lon, ptype)
            total_requests += 1
            for r in raws:
                p = extract(r)
                if p and p["id"] not in seen:
                    seen[p["id"]] = p
            print(f"  +{len(raws)} results, {len(seen)} unique so far")
            time.sleep(0.3)

    places = list(seen.values())
    mean_rating = round(sum(p["rating"] for p in places) / len(places), 4) if places else 0.0

    payload = {
        "meta": {
            "mean_rating": mean_rating,
            "count": len(places),
            "note": "walk_node and bike_node are filled by build_networks.py",
        },
        "places": places,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    print(f"\nWrote {len(places)} places → {OUT_PATH}")
    print(f"Mean rating: {mean_rating}")


if __name__ == "__main__":
    main()
