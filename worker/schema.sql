-- mtl_student_food passive query telemetry.
--
-- Two tables, both fully denormalized. There is deliberately no session id,
-- no user id, and no join key between them: a click carries its own query
-- context so rank-vs-scoring analysis works without anything that links two
-- events to the same person.
--
-- Nothing here is an identifier. cx/cy are integer indices into a ~200 m grid,
-- snapped in the browser before the request is made. ts is server-side.

CREATE TABLE IF NOT EXISTS pin_drops (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  ts            INTEGER NOT NULL,   -- unix seconds, set by the Worker
  schema_v      INTEGER NOT NULL,
  cx            INTEGER NOT NULL,   -- floor(lon / 0.00256)
  cy            INTEGER NOT NULL,   -- floor(lat / 0.0018)
  mode          TEXT    NOT NULL,   -- 'walk' | 'bike'
  cutoff_min    INTEGER NOT NULL,
  weight        REAL    NOT NULL,   -- quality/closeness slider, 0..1
  price         TEXT    NOT NULL,   -- sorted csv of selected tiers 0..4 (Google price_level), e.g. '1,2' or '0,4'; "" means no filter
  view_mode     TEXT    NOT NULL,   -- 'best' | 'gems'
  result_count  INTEGER NOT NULL,
  coverage_m    INTEGER             -- coverage radius at that cell, NULL if unknown
);

CREATE INDEX IF NOT EXISTS pin_drops_cell ON pin_drops (cx, cy);
CREATE INDEX IF NOT EXISTS pin_drops_ts   ON pin_drops (ts);

CREATE TABLE IF NOT EXISTS result_clicks (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  ts            INTEGER NOT NULL,
  schema_v      INTEGER NOT NULL,
  cx            INTEGER NOT NULL,
  cy            INTEGER NOT NULL,
  mode          TEXT    NOT NULL,
  cutoff_min    INTEGER NOT NULL,
  weight        REAL    NOT NULL,
  price         TEXT    NOT NULL,
  view_mode     TEXT    NOT NULL,
  result_count  INTEGER NOT NULL,
  coverage_m    INTEGER,
  clicked_rank  INTEGER NOT NULL    -- 0-based rank in the displayed list
);

CREATE INDEX IF NOT EXISTS result_clicks_cell ON result_clicks (cx, cy);
CREATE INDEX IF NOT EXISTS result_clicks_ts   ON result_clicks (ts);
CREATE INDEX IF NOT EXISTS result_clicks_rank ON result_clicks (clicked_rank);
