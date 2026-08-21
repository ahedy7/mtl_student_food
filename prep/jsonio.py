"""
jsonio.py  –  the only correct way to read and write the JSON data files

Two failure modes this exists to prevent, both of which have actually happened:

1. Encoding. Path.write_text() / read_text() without an explicit encoding uses the
   platform default — cp1252 on Windows. Place names from Google routinely contain
   characters outside it (U+0181 'Ɓ' is the one that bit us), so the write raises
   UnicodeEncodeError. Always UTF-8, everywhere.

2. Truncation. Opening a file for writing truncates it immediately, so a write that
   fails partway leaves nothing behind. A 60-centre pull was lost exactly this way:
   3003 places gathered, then destroyed by the write that was meant to save them.
   So writes go to a temp file first and are swapped in with os.replace(), which is
   atomic. A failed write leaves the previous file untouched.

Use write_json() for anything that took time or money to produce.
"""

import json
import os
import sys
from pathlib import Path


def read_json(path):
    """Read UTF-8 JSON. Raises on missing file — callers should check first."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data, *, compact=True, indent=None):
    """
    Write UTF-8 JSON atomically: temp file in the same directory, then os.replace().

    The temp file must share a filesystem with the target for the replace to be
    atomic, hence same directory rather than the system temp dir.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if compact and indent is None:
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    else:
        text = json.dumps(data, ensure_ascii=False, indent=indent)

    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)          # atomic on POSIX and on Windows (same volume)
    except BaseException:
        # Never leave a stray temp file behind to be mistaken for real data.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


SAMPLE_TEXT = "Ɓé中文\U0001F600"   # the char that broke it, plus friends


def ensure_utf8_stdout() -> None:
    """
    Force stdout/stderr to UTF-8.

    The Windows console is cp1252, so printing an accented place name raises
    UnicodeEncodeError and kills the run — the same failure as the write bug, in
    a progress line. errors="replace" means a character the terminal font cannot
    show degrades to a placeholder instead of aborting a paid API run.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass   # already wrapped, or not reconfigurable; not worth failing over


def preflight(path) -> None:
    """
    Prove that both writing to this location and printing to this console can
    handle non-cp1252 text, before doing any expensive work that would be lost.

    Cheap insurance: either failure otherwise costs a full API pull to discover,
    and both surface in the first second instead of the last.
    """
    ensure_utf8_stdout()

    # 1. console — a progress line must not be able to kill a paid run
    try:
        print(f"  pre-flight console check: {SAMPLE_TEXT}")
    except UnicodeEncodeError as exc:
        raise RuntimeError(
            f"stdout cannot encode non-cp1252 text ({exc}). "
            f"Set PYTHONIOENCODING=utf-8 and retry."
        ) from exc

    # 2. file round-trip
    probe = Path(path).with_name(Path(path).name + ".preflight")
    sample = {"probe": SAMPLE_TEXT}
    try:
        write_json(probe, sample)
        assert read_json(probe) == sample, "round-trip mismatch"
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass
