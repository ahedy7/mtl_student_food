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


def preflight(path) -> None:
    """
    Prove a write of non-cp1252 text to this location works, before doing any
    expensive work that would be lost if it doesn't.

    Cheap insurance: the failure it catches costs a full API pull to discover
    otherwise, and it surfaces in the first second instead of the last.
    """
    probe = Path(path).with_name(Path(path).name + ".preflight")
    sample = {"probe": "Ɓé中文\U0001F600"}   # the char that broke it, plus friends
    try:
        write_json(probe, sample)
        assert read_json(probe) == sample, "round-trip mismatch"
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass
