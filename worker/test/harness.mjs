/**
 * Runs the Worker's fetch handler directly against a fake D1 that records
 * every insert instead of performing one. Needs nothing but Node: no wrangler,
 * no local D1, no dev server.
 *
 * This covers routing, validation, and whether a row would be written. It does
 * NOT prove the SQL is accepted by real D1 or that the schema applies cleanly.
 * Run the wrangler tests for that once Node is installed on the machine.
 */
import worker from "../src/index.js";

let inserted = [];

const env = {
  ALLOW_ORIGIN: "https://ahedy7.github.io",
  DEBUG: "0",
  DB: {
    prepare(sql) {
      return {
        bind(...args) {
          return {
            run: async () => {
              inserted.push({
                table: sql.includes("pin_drops") ? "pin_drops" : "result_clicks",
                args
              });
              return { success: true };
            }
          };
        }
      };
    }
  }
};

const pending = [];
const ctx = { waitUntil: (p) => pending.push(p) };

async function call(method, path, body, extraHeaders = {}) {
  inserted = [];
  pending.length = 0;
  const init = { method, headers: { "Content-Type": "text/plain", ...extraHeaders } };
  if (body !== undefined) init.body = body;
  const res = await worker.fetch(
    new Request("https://w.example" + path, init),
    env,
    ctx
  );
  await Promise.all(pending);
  return { status: res.status, headers: res.headers, rows: inserted.length, inserted };
}

const PIN = {
  v: 1, e: "pin", cx: -28738, cy: 25278, mode: "walk", cutoff: 15,
  weight: 0.6, price: "1,2", view: "best", n: 42, coverage: 283
};
const CLICK = { ...PIN, e: "click", rank: 3 };

const cases = [];
const add = (name, expectRows, expectStatus, run) =>
  cases.push({ name, expectRows, expectStatus, run });

// --- must insert exactly one row ---
add("pin, coverage 283", 1, 204, () => call("POST", "/e", JSON.stringify(PIN)));
add("pin, coverage null", 1, 204, () =>
  call("POST", "/e", JSON.stringify({ ...PIN, coverage: null })));
add("click, rank 3", 1, 204, () => call("POST", "/e", JSON.stringify(CLICK)));

// --- must insert zero rows ---
add("v:2", 0, 204, () => call("POST", "/e", JSON.stringify({ ...PIN, v: 2 })));
add('e:"hover"', 0, 204, () => call("POST", "/e", JSON.stringify({ ...PIN, e: "hover" })));
add("cx/cy in Paris", 0, 204, () =>
  call("POST", "/e", JSON.stringify({ ...PIN, cx: Math.floor(2.35 / 0.00256), cy: Math.floor(48.86 / 0.0018) })));
add('mode:"car"', 0, 204, () => call("POST", "/e", JSON.stringify({ ...PIN, mode: "car" })));
add("weight:1.5", 0, 204, () => call("POST", "/e", JSON.stringify({ ...PIN, weight: 1.5 })));
add("cutoff:0", 0, 204, () => call("POST", "/e", JSON.stringify({ ...PIN, cutoff: 0 })));
add('price:"4"', 0, 204, () => call("POST", "/e", JSON.stringify({ ...PIN, price: "4" })));
add("click, no rank", 0, 204, () => {
  const { rank, ...noRank } = CLICK;
  return call("POST", "/e", JSON.stringify(noRank));
});
add("malformed JSON", 0, 204, () => call("POST", "/e", "{not json"));
add("body > 2048 bytes", 0, 204, () =>
  call("POST", "/e", JSON.stringify({ ...PIN, pad: "x".repeat(3000) })));

// --- extra cases the instructions did not list but that matter ---
add("cx non-integer (float)", 0, 204, () =>
  call("POST", "/e", JSON.stringify({ ...PIN, cx: -28738.5 })));
add("cx as string", 0, 204, () =>
  call("POST", "/e", JSON.stringify({ ...PIN, cx: "-28738" })));
add("payload is an array", 0, 204, () => call("POST", "/e", JSON.stringify([PIN])));
add("payload is null", 0, 204, () => call("POST", "/e", "null"));
add("empty body", 0, 204, () => call("POST", "/e", ""));
add("negative rank", 0, 204, () => call("POST", "/e", JSON.stringify({ ...CLICK, rank: -1 })));
add("rank on a pin event (ignored)", 1, 204, () =>
  call("POST", "/e", JSON.stringify({ ...PIN, rank: 99 })));
add("empty price string", 1, 204, () =>
  call("POST", "/e", JSON.stringify({ ...PIN, price: "" })));
add("weight 0.6666 rounds to 0.67", 1, 204, () =>
  call("POST", "/e", JSON.stringify({ ...PIN, weight: 0.6666 })));
add("NaN weight", 0, 204, () => call("POST", "/e", JSON.stringify({ ...PIN, weight: "abc" })));
add("oversized content-length header", 0, 204, () =>
  call("POST", "/e", JSON.stringify(PIN), { "content-length": "99999" }));

// --- routing ---
add("GET /e", 0, 405, () => call("GET", "/e"));
add("POST /other", 0, 404, () => call("POST", "/other", JSON.stringify(PIN)));
add("OPTIONS /e", 0, 204, () => call("OPTIONS", "/e"));

const rows = [];
let failures = 0;

for (const c of cases) {
  const r = await c.run();
  const ok = r.rows === c.expectRows && r.status === c.expectStatus;
  if (!ok) failures++;
  rows.push({
    case: c.name,
    expected: `${c.expectRows} row / ${c.expectStatus}`,
    actual: `${r.rows} row / ${r.status}`,
    status: ok ? "PASS" : "FAIL"
  });
}

// OPTIONS must carry the CORS header
const opt = await call("OPTIONS", "/e");
const acao = opt.headers.get("Access-Control-Allow-Origin");
const acaoOk = acao === "https://ahedy7.github.io";
if (!acaoOk) failures++;
rows.push({
  case: "OPTIONS /e has ACAO header",
  expected: "https://ahedy7.github.io",
  actual: acao === null ? "(absent)" : acao,
  status: acaoOk ? "PASS" : "FAIL"
});

// weight rounding check on the recorded bind args
const w = await call("POST", "/e", JSON.stringify({ ...PIN, weight: 0.6666 }));
const bound = w.inserted[0] ? w.inserted[0].args[6] : null;
const wOk = bound === 0.67;
if (!wOk) failures++;
rows.push({
  case: "weight 0.6666 stored as 0.67",
  expected: "0.67",
  actual: String(bound),
  status: wOk ? "PASS" : "FAIL"
});

console.table(rows);
console.log(failures === 0 ? "\nALL PASS" : `\n${failures} FAILURE(S)`);
process.exit(failures === 0 ? 0 : 1);
