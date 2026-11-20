# Excerpt from HAVEN (private codebase): apps/core/timeline.py
# The event model and adapter registry. The 20 source adapters and the ingest
# loop are elided. Docstring trimmed of personal examples; code unchanged.
"""One timeline: the Senses layer of HAVEN.

Every source writes its EVENTS here, in one shape: when, how long, what kind,
where, who, a one-line summary and the source's own details. A domain is just
an adapter: a function that reads its own table from a cursor and returns rows.

Two times on every row, and the difference is the point:
  at        when it happened (local wall time, like the calendar)
  seen_at   when HAVEN learned it (local wall time)
The world model can be rebuilt as of any past moment T from the rows seen by T,
which is what lets a real day be replayed as a test: at 6:15 PM on a given day,
what did HAVEN know, and what did it do?

Things that change (a calendar plan, a charge going from pending to posted, a
promise kept) are REVISIONS: same `key`, a new row per change, so "what did the
plan say at 3 AM" has an answer. Readers take the newest revision seen by T.

High-rate streams stay in their own append-only tables; the timeline holds
events, not samples.
"""
import hashlib
import json
from typing import Callable, Dict

SUMMARY_CAP = 300
BATCH = 2000             # rows per adapter per run; the cursor carries the rest

DDL = """
CREATE TABLE IF NOT EXISTS timeline (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    end_at TEXT,
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    ref TEXT NOT NULL,
    key TEXT,
    place TEXT,
    people TEXT,
    summary TEXT,
    data TEXT,
    seen_at TEXT NOT NULL,
    UNIQUE(source, ref));
CREATE INDEX IF NOT EXISTS idx_timeline_at ON timeline(at);
CREATE INDEX IF NOT EXISTS idx_timeline_seen ON timeline(seen_at);
CREATE INDEX IF NOT EXISTS idx_timeline_key ON timeline(key);
CREATE INDEX IF NOT EXISTS idx_timeline_kind_at ON timeline(kind, at);
CREATE TABLE IF NOT EXISTS timeline_cursor (source TEXT PRIMARY KEY, cursor TEXT, ran_at TEXT,
                                            last_error TEXT);
"""


def _rev(key: str, *parts) -> str:
    """A revision ref: same logical key, distinct row per distinct content."""
    return key + "#" + hashlib.sha1(json.dumps(parts, default=str).encode()).hexdigest()[:10]


def _clip(s, n: int = SUMMARY_CAP) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


ADAPTERS: Dict[str, Callable] = {}


def adapter(source: str):
    """fn(conn, cursor) -> (rows, new_cursor). A row: at, kind, ref, summary,
    seen_at, and optionally end_at, key, place, people (list), data (dict)."""
    def reg(fn):
        ADAPTERS[source] = fn
        return fn
    return reg

# ... elided: 20 @adapter(...) sources (calendar, bank, wearables, phone
# location, mail, ...), the incremental ingest loop, and `show` ...
