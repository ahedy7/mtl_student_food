/**
 * mtl_student_food passive query telemetry.
 *
 * Two events, fire-and-forget. Coordinates are snapped to a ~200 m grid here,
 * in the browser, and sent as integer cell indices. A full-precision position
 * never leaves the page.
 *
 * No user id, no session id, no cookie, no storage of any kind. A click event
 * carries a copy of its query context in the payload rather than a key that
 * would tie it back to a pin drop row.
 *
 * Every path in this file swallows its own errors. Telemetry must never be
 * able to break the map.
 */
(function (global) {
  "use strict";

  // Empty string disables everything. Forks and local dev stay silent until
  // this is filled in.
  var ENDPOINT = "";

  var SCHEMA_V = 1;

  // ~200 m each at 45.5 N. A naive 3-decimal round would give 111 m by 78 m,
  // which is not square and quietly distorts any density map built on it.
  var LAT_STEP = 0.0018;
  var LON_STEP = 0.00256;

  // Bounds accidental loops and casual spam. Not a security control; the
  // Worker validates independently.
  var MAX_EVENTS_PER_LOAD = 200;

  // Clicks are throttled only enough to swallow a double-click.
  var MIN_CLICK_INTERVAL_MS = 150;

  // Pin events are debounced on the trailing edge instead of throttled. The
  // sliders in app.js call render() on every 'input' tick, so a single drag
  // fires dozens of times. A throttle would keep whichever value happened to
  // be under the cursor when its window opened, which is a mid-drag sample and
  // not the value the user chose. Waiting for the drag to stop records the
  // resting position, once. This is what makes the measured-defaults query
  // mean anything.
  var PIN_DEBOUNCE_MS = 700;

  var sent = 0;
  var lastClickAt = 0;
  var pinTimer = null;
  var pinPending = null;

  // In memory for the life of the page only. Never persisted, never sent as an
  // identifier; it exists so a click can carry its own context.
  var lastQuery = null;

  function optedOut() {
    try {
      var nav = global.navigator;
      if (!nav) return true;
      if (nav.globalPrivacyControl === true) return true;
      var dnt = nav.doNotTrack || global.doNotTrack || nav.msDoNotTrack;
      return dnt === "1" || dnt === "yes";
    } catch (err) {
      return true;
    }
  }

  var DISABLED = !ENDPOINT || optedOut();

  function cellOf(lat, lon) {
    return {
      cx: Math.floor(lon / LON_STEP),
      cy: Math.floor(lat / LAT_STEP)
    };
  }

  function normPrice(price) {
    // Accepts [1,2] or ['1','2'] or '1,2'. Emits a sorted csv of 1..3.
    try {
      var list = Array.isArray(price) ? price : String(price || "").split(",");
      var seen = {};
      var out = [];
      for (var i = 0; i < list.length; i++) {
        var n = parseInt(list[i], 10);
        if (n >= 1 && n <= 3 && !seen[n]) {
          seen[n] = true;
          out.push(n);
        }
      }
      return out.sort().join(",");
    } catch (err) {
      return "";
    }
  }

  function intOr(v, fallback) {
    var n = Math.round(Number(v));
    return Number.isFinite(n) ? n : fallback;
  }

  function post(payload) {
    if (DISABLED) return;
    try {
      if (sent >= MAX_EVENTS_PER_LOAD) return;
      sent += 1;

      var body = JSON.stringify(payload);

      // text/plain keeps this a simple request. Sending application/json would
      // trigger a CORS preflight, and sendBeacon cannot preflight reliably,
      // least of all during unload.
      var blob = new Blob([body], { type: "text/plain;charset=UTF-8" });

      if (global.navigator && global.navigator.sendBeacon) {
        if (global.navigator.sendBeacon(ENDPOINT, blob)) return;
      }
      if (global.fetch) {
        global.fetch(ENDPOINT, {
          method: "POST",
          body: blob,
          keepalive: true,
          mode: "cors"
        })["catch"](function () {});
      }
    } catch (err) {
      /* never surface */
    }
  }

  function flushPin() {
    try {
      if (pinTimer !== null) {
        global.clearTimeout(pinTimer);
        pinTimer = null;
      }
      if (pinPending) {
        var payload = pinPending;
        pinPending = null;
        post(payload);
      }
    } catch (err) {
      /* never surface */
    }
  }

  function schedulePin(payload) {
    pinPending = payload;
    if (pinTimer !== null) global.clearTimeout(pinTimer);
    pinTimer = global.setTimeout(flushPin, PIN_DEBOUNCE_MS);
  }

  // If the tab is closed or backgrounded mid-drag, send what is queued rather
  // than losing it. This is exactly the case sendBeacon exists for.
  try {
    global.addEventListener("pagehide", flushPin);
    global.addEventListener("visibilitychange", function () {
      if (global.document && global.document.visibilityState === "hidden") flushPin();
    });
  } catch (err) {
    /* never surface */
  }

  /**
   * Call once per pin drop, after results have been computed.
   *
   * opts = {
   *   lat, lon,                 full precision; snapped here and discarded
   *   mode: 'walk' | 'bike',
   *   cutoffMin: 15,
   *   weight: 0.6,              quality/closeness slider
   *   price: [1, 2],            selected tiers
   *   view: 'best' | 'gems',
   *   resultCount: 42,
   *   coverageM: 283            coverage radius at that cell, or null
   * }
   */
  function pinDropped(opts) {
    if (DISABLED) return;
    try {
      if (!opts || typeof opts.lat !== "number" || typeof opts.lon !== "number") return;
      var c = cellOf(opts.lat, opts.lon);
      var weight = Number(opts.weight);
      if (!Number.isFinite(weight)) weight = 0;
      weight = Math.max(0, Math.min(1, Math.round(weight * 100) / 100));

      lastQuery = {
        v: SCHEMA_V,
        cx: c.cx,
        cy: c.cy,
        mode: opts.mode === "bike" ? "bike" : "walk",
        cutoff: intOr(opts.cutoffMin, 15),
        weight: weight,
        price: normPrice(opts.price),
        view: opts.view === "gems" ? "gems" : "best",
        n: Math.max(0, intOr(opts.resultCount, 0)),
        coverage:
          opts.coverageM === null || opts.coverageM === undefined
            ? null
            : Math.max(0, intOr(opts.coverageM, 0))
      };

      var payload = {};
      for (var k in lastQuery) payload[k] = lastQuery[k];
      payload.e = "pin";
      schedulePin(payload);
    } catch (err) {
      /* never surface */
    }
  }

  /**
   * Call when a result is clicked. rank is the 0-based position in the list as
   * displayed. Dropped silently if no pin drop has happened this page load,
   * since there would be no context to attribute it to.
   */
  function resultClicked(rank) {
    if (DISABLED || !lastQuery) return;
    try {
      var r = intOr(rank, -1);
      if (r < 0) return;
      var now = Date.now();
      if (now - lastClickAt < MIN_CLICK_INTERVAL_MS) return;
      lastClickAt = now;
      // A click means the query settled. Send its context now so the pin event
      // is never ordered after the click that came from it.
      flushPin();
      var payload = {};
      for (var k in lastQuery) payload[k] = lastQuery[k];
      payload.e = "click";
      payload.rank = r;
      post(payload);
    } catch (err) {
      /* never surface */
    }
  }

  global.Telemetry = {
    pinDropped: pinDropped,
    resultClicked: resultClicked,
    enabled: !DISABLED
  };
})(window);
