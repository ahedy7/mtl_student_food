# Telemetry collector

Separate deploy from `viz/`. Nothing here touches the Pages build or the
`verify-data` CI gate.

## Deploy

```sh
cd worker
npm install -g wrangler        # if you don't have it
wrangler login

wrangler d1 create mtl_food_telemetry
# paste the printed database_id into wrangler.toml

wrangler d1 execute mtl_food_telemetry --remote --file=./schema.sql
wrangler deploy
```

Then take the deployed URL, append `/e`, and paste it into `ENDPOINT` at the
top of `viz/telemetry.js`.

Smoke test:

```sh
curl -X POST https://<worker>.workers.dev/e \
  -H 'Content-Type: text/plain' \
  -d '{"v":1,"e":"pin","cx":-28738,"cy":25278,"mode":"walk","cutoff":15,"weight":0.6,"price":"1,2","view":"best","n":42,"coverage":283}'

wrangler d1 execute mtl_food_telemetry --remote \
  --command "SELECT * FROM pin_drops ORDER BY id DESC LIMIT 1"
```

Expect `204` and no body. That is also what a rejected payload returns, on
purpose. To see rejection reasons while developing, set `DEBUG = "1"` in
`wrangler.toml`, redeploy, and set it back before you announce the site.

## Two things to do in the dashboard

1. **Add a rate limiting rule.** Security > WAF > Rate limiting rules, matching
   the Worker route, something like 60 requests per minute per IP. This is the
   real defense against someone spraying the endpoint. Cloudflare evaluates it
   at the edge against an IP the Worker never sees, so it costs you nothing in
   retained data.
2. **Leave Logpush off.** The Worker never reads the client IP, but Cloudflare's
   edge receives it on every request regardless. Not retaining it is a
   consequence of Logpush being disabled, not of anything in this code.

## Limits

D1's free tier allows on the order of 100k row writes per day and 5 GB storage.
A pin drop row is well under 100 bytes. Reddit launch traffic will not come
close. The per-load cap in the emitter plus the rate limiting rule are what keep
a bad actor from getting close either.

## Queries

Demand per cell, which is the map itself:

```sql
SELECT cx, cy, COUNT(*) AS drops, AVG(result_count) AS avg_results,
       MAX(coverage_m) AS coverage_m
FROM pin_drops
GROUP BY cx, cy
ORDER BY drops DESC;
```

Sparse area versus data hole. Many drops, few results, and a large coverage
radius means the area genuinely has little food. Many drops, few results, and a
small coverage radius means a cell that capped out and needs another pull:

```sql
SELECT cx, cy, COUNT(*) AS drops, AVG(result_count) AS avg_results, coverage_m
FROM pin_drops
GROUP BY cx, cy, coverage_m
HAVING drops >= 5 AND avg_results < 10
ORDER BY coverage_m ASC, drops DESC;
```

Measured defaults. Compare against the shipped 15 min and 0.6:

```sql
SELECT mode,
       COUNT(*) AS n,
       AVG(cutoff_min) AS mean_cutoff,
       AVG(weight)     AS mean_weight
FROM pin_drops
GROUP BY mode;
```

Click rank distribution, the objective function for the scoring. If clicks
cluster at ranks 0 to 2 the ordering is doing work; if they are flat across the
list, the score is not ranking anything useful:

```sql
SELECT clicked_rank, COUNT(*) AS clicks
FROM result_clicks
GROUP BY clicked_rank
ORDER BY clicked_rank
LIMIT 30;
```

Does the hidden gems view get engaged with, or just looked at:

```sql
SELECT p.view_mode,
       COUNT(DISTINCT p.id) AS drops,
       (SELECT COUNT(*) FROM result_clicks c WHERE c.view_mode = p.view_mode) AS clicks
FROM pin_drops p
GROUP BY p.view_mode;
```

To convert cells back to coordinates for the overlay: `lon = cx * 0.00256`,
`lat = cy * 0.0018` gives the cell's southwest corner. Add half a step for the
centre.
