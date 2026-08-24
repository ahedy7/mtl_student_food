/**
 * Simulates a slider drag against the real telemetry.js to confirm the
 * debounce records the resting value and not a mid-drag sample.
 */
import fs from "fs";
import vm from "vm";

const src = fs.readFileSync(new URL("./telemetry.js", import.meta.url), "utf8");

function load() {
  const posted = [];
  const listeners = {};
  const sandbox = {
    console,
    setTimeout,
    clearTimeout,
    Date,
    Number,
    Math,
    JSON,
    Array,
    String,
    Blob: class { constructor(parts) { this.parts = parts; } },
    document: { visibilityState: "visible" },
    addEventListener: (k, fn) => { listeners[k] = fn; },
    navigator: {
      sendBeacon: (url, blob) => { posted.push(JSON.parse(blob.parts[0])); return true; }
    }
  };
  sandbox.window = sandbox;
  // Enable the module by injecting an endpoint.
  vm.runInNewContext(src.replace('var ENDPOINT = "";', 'var ENDPOINT = "https://w.example/e";'), sandbox);
  return { T: sandbox.Telemetry, posted, listeners };
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

const base = {
  lat: 45.5017, lon: -73.5673, mode: "walk", cutoffMin: 15,
  price: [1, 2], view: "best", resultCount: 42, coverageM: 283
};

// A drag from 0.30 to 0.75 in 20 ticks, 25 ms apart, resting at 0.75.
{
  const { T, posted } = load();
  for (let i = 0; i <= 20; i++) {
    T.pinDropped({ ...base, weight: 0.30 + (0.45 * i) / 20 });
    await wait(25);
  }
  await wait(900);
  console.log("drag of 21 ticks ->", posted.length, "event(s), weight =", posted.map(p => p.weight).join(","));
  console.log(posted.length === 1 && posted[0].weight === 0.75 ? "  PASS: one event at the resting value" : "  FAIL");
}

// Two deliberate queries a second apart stay two events.
{
  const { T, posted } = load();
  T.pinDropped({ ...base, weight: 0.6 });
  await wait(1000);
  T.pinDropped({ ...base, weight: 0.9 });
  await wait(900);
  console.log("two distinct queries ->", posted.length, "event(s), weight =", posted.map(p => p.weight).join(","));
  console.log(posted.length === 2 ? "  PASS: distinct queries preserved" : "  FAIL");
}

// A click during the debounce window flushes the pin first, in order.
{
  const { T, posted } = load();
  T.pinDropped({ ...base, weight: 0.6 });
  await wait(50);
  T.resultClicked(3);
  await wait(900);
  console.log("click mid-window ->", posted.map(p => p.e).join(" then "));
  console.log(posted.length === 2 && posted[0].e === "pin" && posted[1].e === "click"
    ? "  PASS: pin ordered before its click" : "  FAIL");
}

// Leaving the tab mid-drag still sends the queued event.
{
  const { T, posted, listeners } = load();
  T.pinDropped({ ...base, weight: 0.42 });
  await wait(50);
  listeners.pagehide();
  console.log("pagehide mid-drag ->", posted.length, "event(s), weight =", posted.map(p => p.weight).join(","));
  console.log(posted.length === 1 && posted[0].weight === 0.42 ? "  PASS: nothing lost on exit" : "  FAIL");
}

// No full-precision coordinate ever appears in a payload.
{
  const { T, posted } = load();
  T.pinDropped({ ...base, weight: 0.6 });
  await wait(900);
  const keys = Object.keys(posted[0]);
  const leaked = keys.filter(k => /lat|lon|coord/i.test(k));
  const raw = JSON.stringify(posted[0]);
  console.log("payload keys ->", keys.join(","));
  console.log(leaked.length === 0 && !raw.includes("45.5017") && !raw.includes("73.5673")
    ? "  PASS: only grid cells leave the page" : "  FAIL: " + leaked.join(","));
}
