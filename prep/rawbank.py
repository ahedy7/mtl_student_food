"""
rawbank.py  –  append-only log of every raw API response, written before parsing

Why this exists: API responses are the only part of the pipeline that costs money
and cannot be recreated locally. Everything downstream — extract(), dedup, scoring
— is a pure function of them. Banking the raw bytes first makes places.json a
local transform that can be re-run offline, for free, as many times as the parsing
changes. Checkpointing limits how much a crash costs; this removes the cost.

It also makes resume possible: a (centre, type) query that has a completion record
here has already been paid for and is skipped on the next run.

Format: JSON Lines, one object per line, appended and flushed immediately.

  {"kind":"response","centre":[lat,lon,radius],"type":...,"page":0,
   "status":"OK","n":20,"ts":...,"response":{...}}      <- the untouched payload
  {"kind":"query_complete","centre":[lat,lon,radius],"type":...,"pages":3,"n":60}

A query is only banked as complete once all its pages are in, so an interrupted
query is re-fetched rather than silently treated as finished.
"""

import json
import time
from pathlib import Path

BANK_PATH = Path(__file__).parent / "data" / "raw_responses.jsonl"


def _key(centre, place_type):
    lat, lon, radius = centre
    return (round(lat, 5), round(lon, 5), int(radius), place_type)


class RawBank:
    """Append-only writer plus a resume index over what is already banked."""

    def __init__(self, path=BANK_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = None

    # ── reading ──────────────────────────────────────────────────────────────
    def completed(self) -> set:
        """Keys of (centre, type) queries already fully banked."""
        done = set()
        if not self.path.exists():
            return done
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    # A run killed mid-write can leave one torn final line.
                    # Everything before it is still good, so skip and carry on.
                    continue
                if rec.get("kind") == "query_complete":
                    done.add(_key(rec["centre"], rec["type"]))
        return done

    def responses(self):
        """Yield every banked response payload, in the order received."""
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("kind") == "response":
                    yield rec

    def stats(self) -> dict:
        n_resp = n_done = 0
        for line in (self.path.open("r", encoding="utf-8") if self.path.exists() else []):
            if '"kind":"response"' in line or '"kind": "response"' in line:
                n_resp += 1
            elif "query_complete" in line:
                n_done += 1
        return {"responses": n_resp, "queries_complete": n_done,
                "bytes": self.path.stat().st_size if self.path.exists() else 0}

    # ── writing ──────────────────────────────────────────────────────────────
    def _write(self, rec):
        if self._fh is None:
            self._fh = self.path.open("a", encoding="utf-8")
        self._fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._fh.flush()          # survive a hard kill, not just a clean exit

    def record_response(self, centre, place_type, page, data):
        """Bank one raw response. Called before any parsing touches it."""
        self._write({
            "kind": "response",
            "ts": round(time.time(), 3),
            "centre": list(centre),
            "type": place_type,
            "page": page,
            "status": data.get("status"),
            "n": len(data.get("results", [])),
            "response": data,
        })

    def record_query_complete(self, centre, place_type, pages, n):
        self._write({
            "kind": "query_complete",
            "ts": round(time.time(), 3),
            "centre": list(centre),
            "type": place_type,
            "pages": pages,
            "n": n,
        })

    def close(self):
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
