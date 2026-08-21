# Known issues

Logged but not fixed. Each entry states the problem, why it matters, and the
shape of a fix — not a commitment to one.

---

## 1. The isochrone is a convex hull, so it overstates the reachable area

**Where:** `isochronePolygon()` and `convexHull()` in [`viz/app.js`](viz/app.js)

**What happens:** the isochrone overlay is drawn as the convex hull of every graph
node within the cutoff. A convex hull is by definition the smallest *convex* shape
containing those nodes, so it bridges every concavity in the real reachable set.
In Montreal that means the shaded region spans:

- the St. Lawrence and the Lachine Canal, where there is no crossing within the
  cutoff
- Mount Royal, Parc Jean-Drapeau, and other parkland the walk graph only skirts
- rail yards and the Port, which have no through routes
- the gap between two arms of reachable street grid that only connect far outside
  the cutoff

**Why it matters:** the overlay is the most confident-looking element on the map,
and it is the one making the weakest claim. The ranked list is correct — it is
computed per place from actual routed times, and is unaffected by this. But a user
reading the shaded area as "where I can get to" is being misled, most visibly near
water, which in Montreal is most of the interesting edge.

This is cosmetic in the sense that no ranking or travel time is wrong. It is not
cosmetic in the sense that the map is the primary interface.

**Shape of a fix:** replace the convex hull with a concave hull / alpha shape over
the reachable node set. Alpha controls how deeply the boundary is allowed to
follow concavities: too large degenerates to the convex hull, too small shatters
the region into islands around sparse suburban nodes. Alpha likely needs to scale
with the cutoff, since node density within reach grows with it.

Worth considering instead: drop the polygon and shade the reachable *edges*
directly. Less pretty, but it cannot overstate anything, and the data is already
in hand.

**Constraints:** no build step and no new dependencies — any implementation has to
be hand-written in `viz/app.js` alongside the existing `convexHull()`. It runs on
every pin drop and cutoff change, so it shares the render budget with Dijkstra;
the walk graph has ~61k nodes and a 30-minute walk cutoff already reaches several
thousand of them.

---

<details>
<summary>Paste-ready GitHub issue body for #1</summary>

```markdown
### Summary

The isochrone overlay is drawn as a convex hull of the reachable graph nodes
(`isochronePolygon()` / `convexHull()` in `viz/app.js`), so it spans water,
parkland, and rail corridors that are not reachable at all.

### Detail

A convex hull is the smallest convex shape containing the reachable nodes, so it
bridges every concavity in the true reachable set. In Montreal the overlay
visibly spans:

- the St. Lawrence and the Lachine Canal, with no crossing inside the cutoff
- Mount Royal and Parc Jean-Drapeau
- rail yards and the Port
- gaps between arms of street grid that only reconnect outside the cutoff

### Impact

The ranked results are unaffected — those come from per-place routed times. The
problem is that the overlay is the most visually confident element on the map
while making the weakest claim, and it is wrong exactly where Montreal is most
interesting: along the water.

### Possible fix

Replace the convex hull with a concave hull / alpha shape. Alpha probably needs
to scale with the cutoff, since reachable node density grows with it: too large
degenerates back to a convex hull, too small shatters into islands.

Alternative worth weighing: shade reachable edges directly instead of drawing a
polygon. Less attractive, but it cannot overstate the reachable set.

### Constraints

No build step, no new dependencies — this has to be hand-written in `viz/app.js`.
It runs on every pin drop and cutoff change, sharing the render budget with
Dijkstra; a 30-minute walk cutoff already reaches several thousand of the ~61k
walk nodes.
```

</details>
