"""
build_networks.py  –  run once (after pull_places.py)
Downloads walk and bike street networks for central Montreal via osmnx,
exports binary typed-array files the browser can route on, and annotates
each place in viz/data/places.json with its nearest node in each graph.

Binary format
-------------
{prefix}_nodes.bin   – Float32 pairs [lat0, lon0, lat1, lon1, ...]
{prefix}_edges.bin   – Uint32 pairs  [from0, to0, from1, to1, ...]
{prefix}_times.bin   – Uint16        [t0, t1, ...]  seconds, clamped to MAX_TIME_S
{prefix}_meta.json   – metadata object

MAX_TIME_S = 65535 s ≈ 1092 min; no OSM edge in central Montreal comes close.

Usage:
    python prep/build_networks.py           # download from OSM and write binary
    python prep/build_networks.py --convert # convert existing *_graph.json → binary
"""

import argparse
import array
import json
import sys
from datetime import date
from pathlib import Path

from jsonio import ensure_utf8_stdout, read_json, write_json

from snap import SnapError, annotate_places as snap_annotate_places

try:
    import networkx as nx
    import osmnx as ox
    # Allow large single queries and longer timeouts to avoid sub-query storms
    ox.settings.timeout = 300
    _OSM_AVAILABLE = True
except ImportError:
    _OSM_AVAILABLE = False

# Central Montreal bounding box  (north, south, east, west)
# Covers: Plateau, Mile End, Downtown, Old Mtl, Rosemont, Villeray, NDG,
#         Côte-des-Neiges, Hochelaga, Westmount, Monkland, Verdun, Île-des-Sœurs
#
# MUST stay in step with SEARCH_CENTERS in pull_places.py. A place pulled from
# outside this box has no graph node near it, and snapping refuses to place it
# (500 m ceiling) rather than guessing — so widening the centres without
# widening the bbox fails the build instead of shipping wrong travel times.
NORTH  =  45.560
SOUTH  =  45.445
EAST   = -73.505
WEST   = -73.660

WALK_SPEED_MPS = 4800 / 3600    # 4.8 km/h
BIKE_SPEED_MPS = 15000 / 3600   # 15.0 km/h

MAX_TIME_S = 65535  # Uint16 ceiling; ~1092 min — no OSM edge in central Montreal comes close

DATA_DIR = Path(__file__).parent.parent / "viz" / "data"
PLACES_PATH = DATA_DIR / "places.json"


# ── helpers ───────────────────────────────────────────────────────────────────

def graph_to_binary(G: nx.MultiDiGraph, speed_mps: float) -> tuple:
    """
    Convert an osmnx MultiDiGraph to binary typed arrays.

    Returns:
        node_bytes  – array.array("f") tobytes: [lat0, lon0, lat1, lon1, ...] Float32
        edge_bytes  – array.array("I") tobytes: [from0, to0, from1, to1, ...] Uint32
        time_bytes  – array.array("H") tobytes: [t0, t1, ...] Uint16 clamped to MAX_TIME_S
        node_list   – Python list of [lat, lon] for annotate_places
    """
    osm_ids = list(G.nodes())
    id_map = {osm: i for i, osm in enumerate(osm_ids)}
    node_list = [
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

    node_arr = array.array("f")
    for lat, lon in node_list:
        node_arr.append(lat)
        node_arr.append(lon)

    edge_arr = array.array("I")
    time_arr = array.array("H")
    for (u, v), t in edge_min.items():
        edge_arr.append(u)
        edge_arr.append(v)
        time_arr.append(min(MAX_TIME_S, round(t)))

    return node_arr.tobytes(), edge_arr.tobytes(), time_arr.tobytes(), node_list


def write_binary_files(prefix: str, node_bytes: bytes, edge_bytes: bytes,
                       time_bytes: bytes, node_list: list, speed_mps: float,
                       network_type: str):
    """Write the four binary/meta files for a given network prefix."""
    node_count = len(node_bytes) // 8   # 2 floats × 4 bytes each
    edge_count = len(time_bytes) // 2   # 1 Uint16 × 2 bytes each

    # Compute bbox from node_list
    lats = [n[0] for n in node_list]
    lons = [n[1] for n in node_list]
    bbox = {
        "north": max(lats), "south": min(lats),
        "east":  max(lons), "west":  min(lons),
    }

    (DATA_DIR / f"{prefix}_nodes.bin").write_bytes(node_bytes)
    (DATA_DIR / f"{prefix}_edges.bin").write_bytes(edge_bytes)
    (DATA_DIR / f"{prefix}_times.bin").write_bytes(time_bytes)

    meta = {
        "network_type": network_type,
        "speed_kmh": round(speed_mps * 3.6, 1),
        "node_count": node_count,
        "edge_count": edge_count,
        "bbox": bbox,
        "max_time_clamped_s": MAX_TIME_S,
    }
    write_json(DATA_DIR / f"{prefix}_meta.json", meta)

    total_kb = (len(node_bytes) + len(edge_bytes) + len(time_bytes)) / 1024
    print(f"  Wrote {prefix}_nodes.bin / _edges.bin / _times.bin / _meta.json  "
          f"({total_kb:.0f} KB total, {node_count:,} nodes, {edge_count:,} edges)")


# ── main ──────────────────────────────────────────────────────────────────────

def download_and_export(network_type: str, speed_mps: float, prefix: str) -> list:
    """Download graph, export binary files, return node_list for place-snapping."""
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

    node_bytes, edge_bytes, time_bytes, node_list = graph_to_binary(G, speed_mps)
    edge_count = len(time_bytes) // 2
    print(f"  → {len(node_list):,} nodes, {edge_count:,} edges (compact, undirected)")

    write_binary_files(prefix, node_bytes, edge_bytes, time_bytes, node_list,
                       speed_mps, network_type)
    return node_list


def convert_json_to_binary():
    """
    Convert existing walk_graph.json / bike_graph.json → binary format.
    Reads nodes as [lat, lon] pairs and edges as [from, to, sec] triples.
    Reconstructs undirected min-time logic for consistency.
    """
    configs = [
        ("walk", DATA_DIR / "walk_graph.json", WALK_SPEED_MPS),
        ("bike", DATA_DIR / "bike_graph.json", BIKE_SPEED_MPS),
    ]
    results = {}
    for prefix, json_path, speed_mps in configs:
        if not json_path.exists():
            print(f"  {json_path.name} not found – skipping {prefix}")
            continue
        print(f"\n=== Converting {json_path.name} → binary ===")
        payload = read_json(json_path)
        json_nodes = payload["nodes"]   # [[lat, lon], ...]
        json_edges = payload["edges"]   # [[from, to, sec], ...]

        node_list = [[lat, lon] for lat, lon in json_nodes]

        # Reconstruct undirected min-time edges
        edge_min: dict[tuple, int] = {}
        for from_id, to_id, sec in json_edges:
            key = (min(from_id, to_id), max(from_id, to_id))
            if key not in edge_min or sec < edge_min[key]:
                edge_min[key] = sec

        node_arr = array.array("f")
        for lat, lon in node_list:
            node_arr.append(lat)
            node_arr.append(lon)

        edge_arr = array.array("I")
        time_arr = array.array("H")
        for (u, v), t in edge_min.items():
            edge_arr.append(u)
            edge_arr.append(v)
            time_arr.append(min(MAX_TIME_S, t))

        node_bytes = node_arr.tobytes()
        edge_bytes = edge_arr.tobytes()
        time_bytes = time_arr.tobytes()

        write_binary_files(prefix, node_bytes, edge_bytes, time_bytes, node_list,
                           speed_mps, payload.get("meta", {}).get("network_type", prefix))
        results[prefix] = node_list
        print(f"  Converted {len(node_list):,} nodes, {len(edge_min):,} edges")

    return results


def annotate_places(walk_nodes: list, bike_nodes: list, mark_unreachable: bool = False):
    """
    Re-snap every place to the graphs that were just written.

    This MUST run whenever the .bin files change. If the node ids in places.json
    are left pointing at a previous graph's node ordering, every place keeps a
    plausible-looking but wrong travel time, and no cutoff in the app can filter
    it out. snap.annotate_places refuses to write when that has happened.
    """
    if not PLACES_PATH.exists():
        print("\nplaces.json not found – skipping node annotation.")
        print("Run pull_places.py first, then re-run build_networks.py.")
        return

    data = read_json(PLACES_PATH)
    places = data["places"]
    print(f"\nAnnotating {len(places)} places with nearest graph nodes …")

    try:
        stats = snap_annotate_places(places, walk_nodes, bike_nodes,
                                     mark_unreachable=mark_unreachable)
    except SnapError as exc:
        sys.exit(f"\nERROR: {exc}")

    for mode in ("walk", "bike"):
        st = stats[mode]
        print(f"  {mode}: median snap {st['median_m']:.0f} m, "
              f"max {st['max_m']:.0f} m, {st['over_100m']} place(s) over 100 m")
    for u in stats["unreachable"]:
        shown = "no node in range" if u["snap_m"] is None else f"{u['snap_m']} m away"
        print(f"  marked unreachable by {u['mode']}: {u['name']}  ({shown})")

    data.setdefault("meta", {})["annotated"] = date.today().isoformat()
    write_json(PLACES_PATH, data)
    print(f"Updated {PLACES_PATH}")


def main(mark_unreachable: bool = False):
    # Place names contain characters the Windows console (cp1252) cannot
    # encode; without this a progress line can kill a paid run.
    ensure_utf8_stdout()
    if not _OSM_AVAILABLE:
        print("ERROR: osmnx / networkx not installed. Cannot download from OSM.")
        print("Install with: pip install osmnx networkx")
        sys.exit(1)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    walk_nodes = download_and_export("walk", WALK_SPEED_MPS, "walk")
    bike_nodes = download_and_export("bike", BIKE_SPEED_MPS, "bike")
    annotate_places(walk_nodes, bike_nodes, mark_unreachable)
    print("\nDone. Commit viz/data/ and push to deploy.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build walk/bike networks for the viz.")
    parser.add_argument("--convert", action="store_true",
                        help="Convert existing *_graph.json to binary instead of downloading")
    parser.add_argument("--mark-unreachable", action="store_true",
                        help="Record places farther than 500 m from a network as "
                             "unreachable for that mode (null node) instead of failing")
    args = parser.parse_args()

    if args.convert:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        results = convert_json_to_binary()
        print("\nConversion complete. Binary files written to viz/data/.")
        # Writing new .bin files invalidates every node id in places.json, so
        # re-annotation is part of the conversion, not an optional follow-up.
        if "walk" in results and "bike" in results:
            annotate_places(results["walk"], results["bike"], args.mark_unreachable)
        else:
            print("\nWARNING: only some graphs were converted, so places.json was NOT "
                  "re-annotated. Its node ids may point at a stale node ordering - "
                  "run prep/reannotate_places.py before deploying.")
    else:
        main(args.mark_unreachable)
