"""
build_networks.py  –  run once (after pull_places.py)
Downloads walk and bike street networks for central Montreal via osmnx,
exports compact JSON the browser can route on, and annotates each place
in viz/data/places.json with its nearest node in each graph.

Usage:
    python prep/build_networks.py

Outputs:
    viz/data/walk_graph.json
    viz/data/bike_graph.json
    viz/data/places.json   (updated in-place with walk_node / bike_node)
"""

import json
import math
from pathlib import Path

import networkx as nx
import osmnx as ox

# Allow large single queries and longer timeouts to avoid sub-query storms
ox.settings.timeout = 300

# Central Montreal bounding box  (north, south, east, west)
# Covers: Plateau, Mile End, Downtown, Old Mtl, Rosemont, Villeray,
#         NDG, Côte-des-Neiges, Verdun, Hochelaga
NORTH  =  45.555
SOUTH  =  45.475
EAST   = -73.510
WEST   = -73.650

WALK_SPEED_MPS = 4800 / 3600    # 4.8 km/h
BIKE_SPEED_MPS = 15000 / 3600   # 15.0 km/h

DATA_DIR = Path(__file__).parent.parent / "viz" / "data"
PLACES_PATH = DATA_DIR / "places.json"


# ── helpers ───────────────────────────────────────────────────────────────────

def haversine(lat1, lon1, lat2, lon2) -> float:
    """Straight-line distance in metres."""
    R = 6_371_000
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    dφ = math.radians(lat2 - lat1)
    dλ = math.radians(lon2 - lon1)
    a = math.sin(dφ/2)**2 + math.cos(φ1)*math.cos(φ2)*math.sin(dλ/2)**2
    return 2 * R * math.asin(math.sqrt(a))


def nearest_node_idx(nodes_latlon: list, lat: float, lon: float) -> int:
    """Return index of the closest node (squared-degree approximation, O(n))."""
    best_d, best_i = float("inf"), 0
    for i, (nlat, nlon) in enumerate(nodes_latlon):
        d = (nlat - lat)**2 + (nlon - lon)**2
        if d < best_d:
            best_d, best_i = d, i
    return best_i


def graph_to_compact_json(G: nx.MultiDiGraph, speed_mps: float) -> tuple[list, list]:
    """
    Returns (nodes, edges) where:
      nodes  = [[lat, lon], ...]           sequential 0-based integer IDs
      edges  = [[from_id, to_id, seconds], ...]   undirected, min-time per pair
    """
    osm_ids = list(G.nodes())
    id_map = {osm: i for i, osm in enumerate(osm_ids)}
    nodes = [
        [round(G.nodes[n]["y"], 5), round(G.nodes[n]["x"], 5)]
        for n in osm_ids
    ]

    # Collect minimum travel-time for each undirected node pair
    edge_min: dict[tuple, float] = {}
    for u, v, data in G.edges(data=True):
        ui, vi = id_map[u], id_map[v]
        key = (min(ui, vi), max(ui, vi))
        length = data.get("length") or 10.0
        t = length / speed_mps
        if key not in edge_min or t < edge_min[key]:
            edge_min[key] = t

    edges = [[u, v, round(t)] for (u, v), t in edge_min.items()]
    return nodes, edges


# ── main ──────────────────────────────────────────────────────────────────────

def download_and_export(network_type: str, speed_mps: float, out_path: Path) -> list:
    """Download graph, export JSON, return nodes list for place-snapping."""
    print(f"\n=== {network_type.upper()} NETWORK ===")
    print("Downloading from OSM …")
    # osmnx 2.x bbox order: (west, south, east, north)
    G = ox.graph_from_bbox(
        bbox=(WEST, SOUTH, EAST, NORTH),
        network_type=network_type,
        simplify=True,
        retain_all=False,
    )
    print(f"  {G.number_of_nodes():,} nodes, {G.number_of_edges():,} edges (raw)")

    nodes, edges = graph_to_compact_json(G, speed_mps)
    print(f"  → {len(nodes):,} nodes, {len(edges):,} edges (compact, undirected)")

    payload = {
        "meta": {
            "network_type": network_type,
            "speed_kmh": round(speed_mps * 3.6, 1),
            "bbox": {"north": NORTH, "south": SOUTH, "east": EAST, "west": WEST},
        },
        "nodes": nodes,
        "edges": edges,
    }
    out_path.write_text(json.dumps(payload, separators=(",", ":")))

    size_mb = out_path.stat().st_size / 1_048_576
    print(f"  Wrote {out_path.name}  ({size_mb:.1f} MB)")
    return nodes


def annotate_places(walk_nodes: list, bike_nodes: list):
    if not PLACES_PATH.exists():
        print("\nplaces.json not found – skipping node annotation.")
        print("Run pull_places.py first, then re-run build_networks.py.")
        return

    data = json.loads(PLACES_PATH.read_text())
    places = data["places"]
    print(f"\nAnnotating {len(places)} places with nearest graph nodes …")

    for p in places:
        lat, lon = p["lat"], p["lon"]
        wi = nearest_node_idx(walk_nodes, lat, lon)
        bi = nearest_node_idx(bike_nodes, lat, lon)
        p["walk_node"]   = wi
        p["bike_node"]   = bi
        p["walk_snap_m"] = round(haversine(lat, lon, walk_nodes[wi][0], walk_nodes[wi][1]))
        p["bike_snap_m"] = round(haversine(lat, lon, bike_nodes[bi][0], bike_nodes[bi][1]))

    PLACES_PATH.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    print(f"Updated {PLACES_PATH}")


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    walk_nodes = download_and_export(
        "walk", WALK_SPEED_MPS, DATA_DIR / "walk_graph.json"
    )
    bike_nodes = download_and_export(
        "bike", BIKE_SPEED_MPS, DATA_DIR / "bike_graph.json"
    )
    annotate_places(walk_nodes, bike_nodes)
    print("\nDone. Commit viz/data/ and push to deploy.")


if __name__ == "__main__":
    main()
