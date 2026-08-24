/**
 * mtl_student_food passive query telemetry collector.
 *
 * POST /e  with a text/plain body containing JSON. Always answers 204 with no
 * body, whether the payload was accepted or rejected, so a prober learns
 * nothing about what passes validation. Set DEBUG=1 to get reasons back while
 * developing.
 *
 * This Worker never reads CF-Connecting-IP, X-Forwarded-For, or request.cf.
 * The IP still reaches Cloudflare's edge on every request, which is a fact
 * about the network and not something code can change. Keep Logpush disabled
 * so it is not retained anywhere you control. That is a configuration
 * property, not a structural one: say so in DATA.md rather than claiming the
 * whole design is unlinkable by construction.
 */

const SCHEMA_V = 1;
const MAX_BODY_BYTES = 2048;

const LAT_STEP = 0.0018;
const LON_STEP = 0.00256;

// Grid cells outside greater Montreal are junk by definition. This bounds the
// key space cheaply and throws away most scripted noise.
const BOUNDS = { north: 45.70, south: 45.35, west: -73.75, east: -73.40 };
const CX_MIN = Math.floor(BOUNDS.west / LON_STEP);
const CX_MAX = Math.floor(BOUNDS.east / LON_STEP);
const CY_MIN = Math.floor(BOUNDS.south / LAT_STEP);
const CY_MAX = Math.floor(BOUNDS.north / LAT_STEP);

const MODES = new Set(["walk", "bike"]);
const VIEWS = new Set(["best", "gems"]);
const PRICE_RE = /^[0-4](,[0-4])*$/;

function isInt(v) {
  return typeof v === "number" && Number.isInteger(v);
}

function intInRange(v, lo, hi) {
  return isInt(v) && v >= lo && v <= hi;
}

/**
 * Returns { event, row } on success or { error } on failure.
 * Every field is checked. Anything unexpected is a rejection, not a coercion.
 */
function validate(raw) {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    return { error: "not an object" };
  }
  if (raw.v !== SCHEMA_V) return { error: "bad schema version" };

  const event = raw.e;
  if (event !== "pin" && event !== "click") return { error: "bad event type" };

  if (!intInRange(raw.cx, CX_MIN, CX_MAX)) return { error: "cx out of range" };
  if (!intInRange(raw.cy, CY_MIN, CY_MAX)) return { error: "cy out of range" };
  if (!MODES.has(raw.mode)) return { error: "bad mode" };
  if (!VIEWS.has(raw.view)) return { error: "bad view" };
  if (!intInRange(raw.cutoff, 1, 120)) return { error: "cutoff out of range" };
  if (!intInRange(raw.n, 0, 100000)) return { error: "n out of range" };

  if (typeof raw.weight !== "number" || !Number.isFinite(raw.weight)) {
    return { error: "bad weight" };
  }
  if (raw.weight < 0 || raw.weight > 1) return { error: "weight out of range" };

  if (typeof raw.price !== "string") return { error: "bad price" };
  if (raw.price !== "" && !PRICE_RE.test(raw.price)) return { error: "bad price" };

  let coverage = null;
  if (raw.coverage !== null && raw.coverage !== undefined) {
    if (!intInRange(raw.coverage, 0, 50000)) return { error: "coverage out of range" };
    coverage = raw.coverage;
  }

  let rank = null;
  if (event === "click") {
    if (!intInRange(raw.rank, 0, 1000)) return { error: "rank out of range" };
    rank = raw.rank;
  }

  return {
    event,
    row: {
      cx: raw.cx,
      cy: raw.cy,
      mode: raw.mode,
      cutoff: raw.cutoff,
      weight: Math.round(raw.weight * 100) / 100,
      price: raw.price,
      view: raw.view,
      n: raw.n,
      coverage,
      rank,
    },
  };
}

function corsHeaders(env) {
  return {
    "Access-Control-Allow-Origin": env.ALLOW_ORIGIN || "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400",
    "Cache-Control": "no-store",
  };
}

function done(env, reason) {
  const headers = corsHeaders(env);
  if (env.DEBUG === "1" && reason) {
    return new Response(reason, { status: 400, headers });
  }
  return new Response(null, { status: 204, headers });
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders(env) });
    }
    if (url.pathname !== "/e") {
      return new Response(null, { status: 404 });
    }
    if (request.method !== "POST") {
      return new Response(null, { status: 405, headers: corsHeaders(env) });
    }

    const declared = Number(request.headers.get("content-length") || "0");
    if (declared > MAX_BODY_BYTES) return done(env, "body too large");

    let text;
    try {
      text = await request.text();
    } catch (err) {
      return done(env, "unreadable body");
    }
    if (text.length > MAX_BODY_BYTES) return done(env, "body too large");

    let raw;
    try {
      raw = JSON.parse(text);
    } catch (err) {
      return done(env, "bad json");
    }

    const parsed = validate(raw);
    if (parsed.error) return done(env, parsed.error);

    const { event, row } = parsed;
    const ts = Math.floor(Date.now() / 1000);

    const stmt =
      event === "pin"
        ? env.DB.prepare(
            `INSERT INTO pin_drops
               (ts, schema_v, cx, cy, mode, cutoff_min, weight, price, view_mode, result_count, coverage_m)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
          ).bind(
            ts, SCHEMA_V, row.cx, row.cy, row.mode, row.cutoff,
            row.weight, row.price, row.view, row.n, row.coverage
          )
        : env.DB.prepare(
            `INSERT INTO result_clicks
               (ts, schema_v, cx, cy, mode, cutoff_min, weight, price, view_mode, result_count, coverage_m, clicked_rank)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
          ).bind(
            ts, SCHEMA_V, row.cx, row.cy, row.mode, row.cutoff,
            row.weight, row.price, row.view, row.n, row.coverage, row.rank
          );

    // The browser has already moved on; do not make it wait on the write.
    ctx.waitUntil(stmt.run().catch(() => {}));

    return new Response(null, { status: 204, headers: corsHeaders(env) });
  },
};
