"""
graph.py  –  read the binary graphs and route on them, in Python

Mirrors what viz/app.js does at runtime (load .bin -> CSR -> Dijkstra) so prep-side
checks can reproduce what a user would actually see. Stdlib only, so CI can run it
with no pip install.

This is a verification tool, not part of the build. Nothing here writes files.
"""

import array
import heapq
import json
import math
from pathlib import Path

from snap import GRID_DEG, MAX_SNAP_M, SPEED_MPS, haversine

DATA_DIR = Path(__file__).parent.parent / "viz" / "data"


class Graph:
    """One network (walk or bike): node coordinates, adjacency, and a snap grid."""

    def __init__(self, prefix: str):
        self.prefix = prefix
        self.speed_mps = SPEED_MPS[prefix]

        nodes = array.array("f")
        nodes.frombytes((DATA_DIR / f"{prefix}_nodes.bin").read_bytes())
        edges = array.array("I")
        edges.frombytes((DATA_DIR / f"{prefix}_edges.bin").read_bytes())
        times = array.array("H")
        times.frombytes((DATA_DIR / f"{prefix}_times.bin").read_bytes())

        self.nodes = [(nodes[i * 2], nodes[i * 2 + 1]) for i in range(len(nodes) // 2)]
        self.meta = json.loads((DATA_DIR / f"{prefix}_meta.json").read_text(encoding="utf-8"))

        n = len(self.nodes)
        self.adj = [[] for _ in range(n)]
        for k in range(len(times)):
            u, v, t = edges[k * 2], edges[k * 2 + 1], times[k]
            self.adj[u].append((v, t))
            self.adj[v].append((u, t))

        self.grid = {}
        for i, (lat, lon) in enumerate(self.nodes):
            key = (math.floor(lat / GRID_DEG), math.floor(lon / GRID_DEG))
            self.grid.setdefault(key, []).append(i)

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    def nearest(self, lat: float, lon: float) -> "tuple[int, float]":
        """Snap a pin the way app.js does: nearest node in the 3x3 cell block."""
        br, bc = math.floor(lat / GRID_DEG), math.floor(lon / GRID_DEG)
        best_d, best_i = float("inf"), -1
        for dr in range(-3, 4):
            for dc in range(-3, 4):
                for i in self.grid.get((br + dr, bc + dc), ()):
                    nlat, nlon = self.nodes[i]
                    d = (nlat - lat) ** 2 + (nlon - lon) ** 2
                    if d < best_d:
                        best_d, best_i = d, i
        if best_i < 0:
            raise ValueError(f"no {self.prefix} node near ({lat}, {lon})")
        snap_m = haversine(lat, lon, *self.nodes[best_i])
        # Mirrors MAX_PIN_SNAP_M in viz/app.js: past 500 m the nearest node is
        # somewhere the user cannot actually reach (across water, typically).
        if snap_m > MAX_SNAP_M:
            raise ValueError(f"nearest {self.prefix} node is {snap_m:.0f} m away "
                             f"(limit {MAX_SNAP_M} m) from ({lat}, {lon})")
        return best_i, snap_m

    def dijkstra(self, start: int, cutoff_sec: float) -> list:
        """Travel time in seconds from `start`; inf past the cutoff."""
        INF = float("inf")
        dist = [INF] * len(self.nodes)
        dist[start] = 0.0
        pq = [(0.0, start)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist[u]:
                continue
            if d > cutoff_sec:
                break
            for v, w in self.adj[u]:
                nd = d + w
                if nd < dist[v]:
                    dist[v] = nd
                    heapq.heappush(pq, (nd, v))
        return dist

    def reachable(self, places: list, pin_lat: float, pin_lon: float,
                  cutoff_sec: float) -> list:
        """
        Places reachable from the pin within the cutoff, as [(place, total_sec)].

        Applies the same three legs and the same node-id validation as
        filterAndScore in viz/app.js, so results here match what a user sees.
        """
        start, pin_snap_m = self.nearest(pin_lat, pin_lon)
        pin_snap_sec = pin_snap_m / self.speed_mps
        dist = self.dijkstra(start, cutoff_sec)

        node_key = f"{self.prefix}_node"
        snap_key = f"{self.prefix}_snap_m"
        out = []
        for p in places:
            idx = p.get(node_key)
            if not isinstance(idx, int) or not (0 <= idx < len(self.nodes)):
                continue
            graph_sec = dist[idx]
            if graph_sec == float("inf"):
                continue
            total = graph_sec + pin_snap_sec + (p.get(snap_key) or 0) / self.speed_mps
            if total <= cutoff_sec:
                out.append((p, total))
        return out
