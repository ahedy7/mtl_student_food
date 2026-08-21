"""
reannotate_places.py  –  free, no API calls, no downloads

Recomputes walk_node / bike_node / walk_snap_m / bike_snap_m for every place in
viz/data/places.json by snapping against the *existing* viz/data/*_nodes.bin
files. Touches nothing else in places.json.

Use this when the node ids in places.json have drifted out of sync with the
binary graphs — e.g. after regenerating the .bin files without re-running the
annotation step. Symptom in the app: places show plausible travel times but sit
kilometres away, and lowering the cutoff slider does not remove them, because
the times are read from the wrong node.

If you changed the network bbox, run build_networks.py instead — that rebuilds
the graphs and re-annotates in one pass.

Usage:
    python prep/reannotate_places.py            # rewrite places.json in place
    python prep/reannotate_places.py --dry-run  # report only, write nothing
    python prep/reannotate_places.py --mark-unreachable   # allow off-network places
"""

import argparse
import array
import json
import sys
from pathlib import Path

from jsonio import ensure_utf8_stdout, read_json, write_json

from graph import Graph
from snap import (BOUND_SPEED_FACTOR, SnapError, annotate_places, check_crow_bound,
                  verify_annotation)

DATA_DIR = Path(__file__).parent.parent / "viz" / "data"
PLACES_PATH = DATA_DIR / "places.json"


def load_nodes(prefix: str) -> list:
    """Read {prefix}_nodes.bin → [(lat, lon), ...], same order as the browser sees."""
    path = DATA_DIR / f"{prefix}_nodes.bin"
    if not path.exists():
        sys.exit(f"{path} not found — run build_networks.py first.")
    flat = array.array("f")
    flat.frombytes(path.read_bytes())
    if len(flat) % 2:
        sys.exit(f"{path.name} has an odd float count ({len(flat)}) — file is truncated.")
    nodes = [(flat[i * 2], flat[i * 2 + 1]) for i in range(len(flat) // 2)]

    meta_path = DATA_DIR / f"{prefix}_meta.json"
    if meta_path.exists():
        declared = read_json(meta_path).get("node_count")
        if declared is not None and declared != len(nodes):
            sys.exit(f"{prefix}: {meta_path.name} declares {declared} nodes but "
                     f"{path.name} holds {len(nodes)} — the two are out of sync.")
    return nodes


# Pins spread across the covered area, each exercising a different part of the
# graph: dense core, riverfront, west end, north end. Kept small so --check stays
# quick enough to gate a deploy.
SAMPLE_PINS = [
    ("Plateau",      45.5236, -73.5803),
    ("Downtown",     45.5048, -73.5772),
    ("Old Montreal", 45.5076, -73.5539),
    ("NDG",          45.4860, -73.6261),
    ("Villeray",     45.5459, -73.6100),
    # Southern coverage, added with the widened bbox. These matter most: they are
    # the newest part of the graph, and Ile-des-Soeurs is an island whose walk
    # network connects to the mainland only over bridges.
    ("Verdun",       45.4620, -73.5720),
    ("Ile-des-Soeurs", 45.4570, -73.5480),
    ("Westmount",    45.4870, -73.5980),
]
SAMPLE_CUTOFFS_MIN = (10, 30)


def check_sample_pins(places: list) -> bool:
    """
    Route from each sample pin and assert the straight-line reachability bound.

    verify_annotation() proves the stored ids match the current .bin files. This
    proves the answers those ids produce are physically possible — an invariant
    that holds no matter how the graph is stored, so it survives changes to the
    binary format, the CSR build, or Dijkstra itself.
    """
    print("\nChecking straight-line bound from sample pins "
          f"(x{BOUND_SPEED_FACTOR:g} mode speed) …")

    total_violations = 0
    gaps = 0
    for mode in ("walk", "bike"):
        graph = Graph(mode)
        for label, lat, lon in SAMPLE_PINS:
            for cutoff_min in SAMPLE_CUTOFFS_MIN:
                cutoff_sec = cutoff_min * 60
                try:
                    results = graph.reachable(places, lat, lon, cutoff_sec)
                except ValueError as exc:
                    # No node near the pin. Report it as a coverage gap rather than
                    # crashing — the app now shows the user the same thing.
                    print(f"  GAP  {mode:4} {label:15} {cutoff_min:2d} min  "
                          f"no {mode} network at this pin ({exc})")
                    gaps += 1
                    continue
                violations = check_crow_bound(lat, lon, results, mode, cutoff_sec)
                flag = "FAIL" if violations else "ok"
                print(f"  {flag:4} {mode:4} {label:13} {cutoff_min:2d} min  "
                      f"{len(results):4d} reachable, {len(violations)} violation(s)")
                for v in violations[:5]:
                    print(f"         {v['name'][:38]:38} {v['crow_m']:6.0f} m away, "
                          f"reported {v['reported_min']:.1f} min "
                          f"(implies {v['implied_kmh']:.0f} km/h, limit {v['limit_m']:.0f} m)")
                total_violations += len(violations)

    if total_violations:
        print(f"\nFAIL: {total_violations} result(s) violate the straight-line bound. "
              f"Reported travel times are not physically possible — the routing or "
              f"the node ids are wrong.")
        return False
    return True


def main():
    # Place names contain characters the Windows console (cp1252) cannot
    # encode; without this a progress line can kill a paid run.
    ensure_utf8_stdout()
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change without writing places.json")
    parser.add_argument("--mark-unreachable", action="store_true",
                        help="Record places farther than 500 m from a network as "
                             "unreachable for that mode (null node) instead of failing")
    parser.add_argument("--check", action="store_true",
                        help="Verify the node ids already in places.json against the "
                             ".bin graphs and exit non-zero if they are stale. "
                             "Writes nothing. Suitable for CI.")
    args = parser.parse_args()

    if not PLACES_PATH.exists():
        sys.exit(f"{PLACES_PATH} not found — run pull_places.py first.")

    data = read_json(PLACES_PATH)
    places = data["places"]

    walk_nodes = load_nodes("walk")
    bike_nodes = load_nodes("bike")
    print(f"Loaded {len(walk_nodes):,} walk nodes, {len(bike_nodes):,} bike nodes")
    print(f"Snapping {len(places)} places …")

    if args.check:
        try:
            stats = verify_annotation(places, walk_nodes, bike_nodes)
        except SnapError as exc:
            sys.exit(f"\nFAIL: {exc}")
        for mode in ("walk", "bike"):
            s = stats[mode]
            print(f"  {mode}: median snap {s['median_m']:.0f} m, max {s['max_m']:.0f} m, "
                  f"{s['over_100m']} over 100 m, {s['unreachable']} marked unreachable")

        if not check_sample_pins(places):
            sys.exit(1)

        print("\nOK: node ids match the binary graphs, and all sample pins "
              "satisfy the straight-line bound.")
        return

    before = {p["id"]: (p.get("walk_node"), p.get("bike_node")) for p in places}

    try:
        stats = annotate_places(places, walk_nodes, bike_nodes,
                                mark_unreachable=args.mark_unreachable)
    except SnapError as exc:
        sys.exit(f"\nERROR: {exc}")

    for mode in ("walk", "bike"):
        s = stats[mode]
        print(f"  {mode}: median snap {s['median_m']:.0f} m, "
              f"max {s['max_m']:.0f} m, {s['over_100m']} place(s) over 100 m")

    for u in stats["unreachable"]:
        shown = "no node in range" if u["snap_m"] is None else f"{u['snap_m']} m away"
        print(f"  marked unreachable by {u['mode']}: {u['name']}  ({shown})")

    changed = sum(1 for p in places
                  if before[p["id"]] != (p["walk_node"], p["bike_node"]))
    print(f"  node id changed for {changed} of {len(places)} places")

    if args.dry_run:
        print("\n--dry-run: places.json not written.")
        return

    write_json(PLACES_PATH, data)
    print(f"\nWrote {PLACES_PATH}")


if __name__ == "__main__":
    main()
