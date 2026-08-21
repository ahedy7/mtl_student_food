"""
snap.py  –  shared nearest-node snapping for build_networks.py and reannotate_places.py

Both scripts must assign place → graph-node ids the same way, so the logic lives
here rather than being duplicated (and drifting) in two places.

Why this exists: a place's travel time is read straight out of the Dijkstra
distance array at its stored node id. If that id points at the wrong node, the
place shows a plausible-but-false travel time and no cutoff can filter it out —
the failure is silent. So snapping fails loudly instead of falling back.

Guarantees:
  * nearest_node() returns the true nearest node, not a grid approximation.
    The expanding-ring search only stops once the best candidate found is
    closer than anything the unsearched rings could still contain.
  * annotate_places() raises SnapError if any place snaps farther than
    MAX_SNAP_M, or if the median snap is worse than MEDIAN_SNAP_MAX_M.
    It never falls back to node 0.
"""

import math
import statistics

# Grid cell size in degrees. 0.002° ≈ 222 m of latitude at Montreal's latitude —
# matches GRID_DEG in viz/app.js so both sides bucket nodes the same way.
GRID_DEG = 0.002

# A place farther than this from any graph node is a data error, not a long
# driveway: either the network bbox does not cover it or the graph is wrong.
MAX_SNAP_M = 500

# Sanity ceiling on the median. Central Montreal's walk graph is dense enough
# that the true median is ~27 m; anything near 50 m means the node ids are being
# matched against the wrong node ordering.
MEDIAN_SNAP_MAX_M = 50

_M_PER_DEG_LAT = 111_320.0

# Travel speeds. build_networks.py imports these so the graph edge times, the
# prep-side checks, and viz/app.js (WALK_MPS / BIKE_MPS) all agree.
WALK_SPEED_MPS = 4800 / 3600     # 4.8 km/h
BIKE_SPEED_MPS = 15000 / 3600    # 15.0 km/h
SPEED_MPS = {"walk": WALK_SPEED_MPS, "bike": BIKE_SPEED_MPS}

# Multiplier on the mode speed for the straight-line reachability bound below.
# Strictly the bound holds at 1.0 — no route beats a straight line — but edge
# times are rounded to whole seconds in Uint16 and node coordinates to Float32,
# so a little headroom keeps the guard from firing on rounding. 2x means a
# "10-minute walk" would have to average 9.6 km/h before it trips.
BOUND_SPEED_FACTOR = 2.0


class SnapError(RuntimeError):
    """Raised when place → node snapping produces results that cannot be trusted."""


class BoundError(RuntimeError):
    """Raised when a result violates the straight-line reachability bound."""


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    R = 6_371_000
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    dφ = math.radians(lat2 - lat1)
    dλ = math.radians(lon2 - lon1)
    a = math.sin(dφ/2)**2 + math.cos(φ1)*math.cos(φ2)*math.sin(dλ/2)**2
    return 2 * R * math.asin(math.sqrt(a))


def build_node_grid(nodes: list) -> dict:
    """Bucket node indices into GRID_DEG cells. nodes is a list of (lat, lon)."""
    grid: dict = {}
    for i, (lat, lon) in enumerate(nodes):
        grid.setdefault((math.floor(lat / GRID_DEG), math.floor(lon / GRID_DEG)), []).append(i)
    return grid


def nearest_node(grid: dict, nodes: list, lat: float, lon: float) -> "tuple[int, float]":
    """
    Return (index, distance_m) of the node nearest (lat, lon).

    Searches outward one ring of grid cells at a time. A ring at radius r can
    contain nothing closer than (r-1) cell widths, so once the best hit so far
    beats that bound the answer is final — this is exact, not approximate.
    """
    if not nodes:
        raise SnapError("empty node list")

    m_per_deg_lon = _M_PER_DEG_LAT * math.cos(math.radians(lat)) or 1.0
    cell_m = GRID_DEG * min(_M_PER_DEG_LAT, m_per_deg_lon)

    br, bc = math.floor(lat / GRID_DEG), math.floor(lon / GRID_DEG)
    best_d, best_i = float("inf"), -1

    # Cap the expansion so a place far outside the graph terminates instead of
    # scanning the whole grid; MAX_SNAP_M is enforced by the caller anyway.
    max_ring = int(math.ceil(MAX_SNAP_M / cell_m)) + 2

    ring = 0
    while ring <= max_ring:
        if ring == 0:
            cells = [(br, bc)]
        else:
            cells = []
            for d in range(-ring, ring + 1):
                cells.append((br - ring, bc + d))
                cells.append((br + ring, bc + d))
            for d in range(-ring + 1, ring):
                cells.append((br + d, bc - ring))
                cells.append((br + d, bc + ring))

        for cell in cells:
            for i in grid.get(cell, ()):
                nlat, nlon = nodes[i]
                d = haversine(lat, lon, nlat, nlon)
                if d < best_d:
                    best_d, best_i = d, i

        # Nothing in an unsearched ring can be closer than this bound.
        if best_i >= 0 and best_d <= ring * cell_m:
            break
        ring += 1

    if best_i < 0:
        raise SnapError(
            f"no graph node within {max_ring * cell_m:.0f} m of ({lat}, {lon})"
        )
    return best_i, best_d


def max_crow_distance_m(mode: str, seconds: float,
                        factor: float = BOUND_SPEED_FACTOR) -> float:
    """Farthest a result may plausibly be, straight-line, within `seconds`."""
    return SPEED_MPS[mode] * factor * seconds


def check_crow_bound(pin_lat: float, pin_lon: float, results: list, mode: str,
                     cutoff_sec: float, factor: float = BOUND_SPEED_FACTOR) -> list:
    """
    Assert the one invariant that does not depend on how the graph is stored:
    a place reachable within `cutoff_sec` cannot be farther from the pin, as the
    crow flies, than `cutoff_sec` x speed. A real route is at least as long as
    the straight line, so any violation means the reported time is fiction —
    whatever the cause.

    verify_annotation() only catches ids that disagree with the current .bin
    files. This catches a wrong answer regardless of mechanism: a changed binary
    layout, a bad CSR build, an off-by-one in Dijkstra, a unit mix-up.

    `results` is an iterable of (place, total_sec). Returns a list of violation
    dicts, empty when everything checks out. Does not raise — callers decide
    whether a violation is fatal.
    """
    limit_m = max_crow_distance_m(mode, cutoff_sec, factor)
    violations = []
    for place, total_sec in results:
        crow = haversine(pin_lat, pin_lon, place["lat"], place["lon"])
        if crow > limit_m:
            violations.append({
                "name": place.get("name", "?"),
                "crow_m": crow,
                "limit_m": limit_m,
                "reported_min": total_sec / 60,
                "implied_kmh": (crow / total_sec) * 3.6 if total_sec > 0 else float("inf"),
            })
    violations.sort(key=lambda v: -v["crow_m"])
    return violations


def verify_annotation(places: list, walk_nodes: list, bike_nodes: list,
                      median_snap_max_m: float = MEDIAN_SNAP_MAX_M) -> dict:
    """
    Check the node ids ALREADY stored on each place, without recomputing them.

    annotate_places() cannot detect stale ids — it overwrites them, so whatever
    it writes is correct by construction. This function is the one that catches
    the failure that actually shipped: places.json holding ids from one graph
    while viz/data/*.bin holds a different node ordering. Every place then has a
    plausible but wrong travel time and no cutoff filters it out.

    Raises SnapError if a median snap exceeds median_snap_max_m or any stored id
    is out of range. Returns per-mode stats otherwise. Never mutates `places`.
    """
    node_sets = {"walk": walk_nodes, "bike": bike_nodes}
    stats = {}
    problems = []

    for mode, nodes in node_sets.items():
        n = len(nodes)
        dists = []
        out_of_range = 0
        missing = 0
        for p in places:
            idx = p.get(f"{mode}_node")
            if idx is None:
                missing += 1          # explicitly off-network; not an error
                continue
            if not isinstance(idx, int) or not (0 <= idx < n):
                out_of_range += 1
                continue
            nlat, nlon = nodes[idx]
            dists.append(haversine(p["lat"], p["lon"], nlat, nlon))

        if out_of_range:
            problems.append(f"{mode}: {out_of_range} place(s) have a {mode}_node id "
                            f"outside 0..{n-1}")
        if not dists:
            problems.append(f"{mode}: no place has a usable {mode}_node id")
            continue

        med = statistics.median(dists)
        stats[mode] = {
            "median_m": med,
            "max_m": max(dists),
            "over_100m": sum(1 for d in dists if d > 100),
            "unreachable": missing,
            "out_of_range": out_of_range,
        }
        if med > median_snap_max_m:
            problems.append(
                f"{mode}: median snap is {med:.0f} m (limit {median_snap_max_m:.0f} m) - "
                f"the stored node ids do not match {mode}_nodes.bin"
            )

    if problems:
        raise SnapError(
            "places.json node ids are out of sync with the binary graphs:\n\n  "
            + "\n  ".join(problems)
            + "\n\nRun: python prep/reannotate_places.py --mark-unreachable"
        )
    return stats


def annotate_places(places: list, walk_nodes: list, bike_nodes: list,
                    max_snap_m: float = MAX_SNAP_M,
                    median_snap_max_m: float = MEDIAN_SNAP_MAX_M,
                    mark_unreachable: bool = False) -> dict:
    """
    Set walk_node / bike_node / walk_snap_m / bike_snap_m on each place in-place.

    Raises SnapError — writing nothing — if any place exceeds max_snap_m or if a
    median snap exceeds median_snap_max_m. Mutates `places` only after every
    place has passed, so a failed run leaves the caller's data untouched.

    mark_unreachable=True downgrades the per-place distance check: instead of
    raising, an over-threshold place/mode gets node = None and snap_m = None,
    meaning "not on this network". Callers must treat a null node as unreachable.
    Use this only for places that genuinely sit off a network (e.g. inside a park
    the bike graph does not enter), never to paper over a whole-file id mismatch —
    the median check still raises, and that is what catches drift.
    """
    grids = {"walk": build_node_grid(walk_nodes), "bike": build_node_grid(bike_nodes)}
    node_sets = {"walk": walk_nodes, "bike": bike_nodes}

    resolved: list = []      # [(place, {mode: (idx, dist)}), ...]
    offenders: list = []

    for p in places:
        lat, lon = p["lat"], p["lon"]
        per_mode = {}
        for mode in ("walk", "bike"):
            try:
                idx, dist = nearest_node(grids[mode], node_sets[mode], lat, lon)
            except SnapError as exc:
                offenders.append((p, mode, float("inf"), str(exc)))
                continue
            per_mode[mode] = (idx, dist)
            if dist > max_snap_m:
                offenders.append((p, mode, dist, ""))
        resolved.append((p, per_mode))

    if offenders and not mark_unreachable:
        lines = [
            f"{len(offenders)} place/mode pair(s) snapped farther than {max_snap_m:.0f} m "
            f"from the street network. Refusing to write.",
            "",
            "This usually means the network bbox does not cover these places. "
            "Widen the bbox in build_networks.py and rebuild, or drop the places.",
            "If these places genuinely sit off the network, re-run with "
            "--mark-unreachable to record them as unreachable for that mode.",
            "",
        ]
        for p, mode, dist, msg in offenders[:20]:
            shown = "unreachable" if dist == float("inf") else f"{dist:8.0f} m"
            lines.append(f"  {mode:4}  {shown}  {p.get('name','?')[:40]:40}  "
                         f"({p['lat']:.5f}, {p['lon']:.5f}) {msg}")
        if len(offenders) > 20:
            lines.append(f"  … and {len(offenders) - 20} more")
        raise SnapError("\n".join(lines))

    # Drop over-threshold pairs so they are neither written nor counted in the
    # median — a place 560 m off the bike graph should not skew the health check.
    dropped = {(id(p), mode) for p, mode, _, _ in offenders}
    for p, per in resolved:
        for mode in ("walk", "bike"):
            if (id(p), mode) in dropped:
                per.pop(mode, None)

    stats = {"unreachable": [
        {"name": p.get("name", "?"), "mode": mode,
         "snap_m": None if dist == float("inf") else round(dist)}
        for p, mode, dist, _ in offenders
    ]}
    for mode in ("walk", "bike"):
        dists = [per[mode][1] for _, per in resolved if mode in per]
        if not dists:
            raise SnapError(f"no place could be snapped to the {mode} network")
        med = statistics.median(dists)
        stats[mode] = {
            "median_m": med,
            "max_m": max(dists),
            "over_100m": sum(1 for d in dists if d > 100),
        }
        if med > median_snap_max_m:
            raise SnapError(
                f"{mode} median snap distance is {med:.0f} m (limit {median_snap_max_m:.0f} m). "
                f"The node ids do not match this graph's node ordering — "
                f"regenerate the .bin files and re-annotate together."
            )

    # All checks passed; now it is safe to mutate.
    for p, per in resolved:
        for mode in ("walk", "bike"):
            if mode in per:
                idx, dist = per[mode]
                p[f"{mode}_node"] = idx
                p[f"{mode}_snap_m"] = round(dist)
            else:
                # Off this network: null node means unreachable to the app.
                p[f"{mode}_node"] = None
                p[f"{mode}_snap_m"] = None

    return stats
