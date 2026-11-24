"""Durable, bounded-memory highlight analysis for long recordings."""
from contextlib import closing
import json
import sqlite3
import zlib

from . import media
from .ranking import features, rank

FORMAT = "window-index-1"


def index_path(directory):
    return directory / "analysis.sqlite"


def reader(directory):
    return closing(sqlite3.connect(index_path(directory).resolve().as_uri() + "?mode=ro", uri=True))


def save_window(db, source, metadata, directory, start, end):
    signals = media.analyze_window(source, metadata, directory, start, end)
    blob = zlib.compress(json.dumps(signals, separators=(",", ":")).encode(), 1)
    with db:
        db.execute("INSERT INTO windows(start,signals,notice) VALUES (?,?,?)",
                   (start, blob, signals["notice"]))
        db.executemany("INSERT INTO speech VALUES(?,?,?)", (
            (start+cue["start"], start+cue["end"], cue["text"]) for cue in signals["speech"]
        ))


def score_window(db, start, size, duration, selector):
    # Use an entire neighbor on each side so events, pauses and captions
    # can straddle a decoder window. The midpoint owns the proposal.
    origin = max(0, start-size)
    stop = min(duration, start+2*size)
    records = db.execute("SELECT start,signals FROM windows WHERE start>=? AND start<? ORDER BY start",
                         (origin, stop))
    signals = media.combine((at-origin, json.loads(zlib.decompress(blob))) for at, blob in records)
    candidates = [row for row in media.propose(signals, stop-origin)
                  if start <= origin+(row[0]+row[1])//2 < start+size]
    ordered, version = rank(signals, candidates, selector)
    with db:
        for row, score in ordered:
            a, b, _, reason = row
            db.execute("INSERT INTO candidates VALUES(?,?,?,?,?,?) ON CONFLICT(start,end,reason) DO NOTHING", (
                origin+a, origin+b, reason, float(score), json.dumps(features(signals, row)),
                media.scene_signature(signals, a, b)
            ))
        db.execute("UPDATE windows SET scored=1, selector=? WHERE start=?",
                   (version if ordered else None, start))


def build(source, metadata, directory, selector, is_cancelled):
    """Checkpoint each decoded window, then score candidates with neighboring context."""
    duration = metadata["duration_ms"]
    with closing(sqlite3.connect(index_path(directory))) as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS config (window_ms INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS windows (
                start INTEGER PRIMARY KEY, signals BLOB NOT NULL, notice TEXT, scored INTEGER DEFAULT 0,
                selector TEXT
            );
            CREATE TABLE IF NOT EXISTS candidates (
                start INTEGER, end INTEGER, reason TEXT, score REAL, features TEXT, signature BLOB,
                PRIMARY KEY(start, end, reason)
            );
            CREATE INDEX IF NOT EXISTS ranked ON candidates(score DESC, start, end, reason);
            CREATE TABLE IF NOT EXISTS speech (start INTEGER, end INTEGER, text TEXT);
            CREATE INDEX IF NOT EXISTS speech_start ON speech(start);
        """)
        with db:
            db.execute("INSERT INTO config SELECT ? WHERE NOT EXISTS (SELECT 1 FROM config)", (media.WINDOW_MS,))
        size = db.execute("SELECT window_ms FROM config").fetchone()[0]
        for start in range(0, duration, size):
            if is_cancelled():
                return None
            if db.execute("SELECT 1 FROM windows WHERE start=?", (start,)).fetchone():
                continue
            save_window(db, source, metadata, directory, start, min(start+size, duration))

        for start in range(0, duration, size):
            if is_cancelled():
                return None
            if db.execute("SELECT scored FROM windows WHERE start=?", (start,)).fetchone()[0]:
                continue
            score_window(db, start, size, duration, selector)
        versions = db.execute("SELECT DISTINCT selector FROM windows WHERE selector IS NOT NULL").fetchall()
        if len(versions) > 1:
            raise ValueError("The highlight model changed during analysis. Start a new run with one selector.")
        notice = db.execute("SELECT notice FROM windows WHERE notice IS NOT NULL LIMIT 1").fetchone()
        return {"format": FORMAT, "selector": versions[0][0] if versions else "rules-2",
                "notice": notice[0] if notice else None}


def picks(directory, count):
    with reader(directory) as db:
        rows = db.execute("SELECT start,end,reason,signature FROM candidates ORDER BY score DESC,start,end,reason")
        return media.distinct_picks(rows, count)


def cues(directory, start, end):
    with reader(directory) as db:
        size = db.execute("SELECT window_ms FROM config").fetchone()[0]
        rows = db.execute("SELECT start,end,text FROM speech WHERE start>=? AND start<? AND end>? ORDER BY start",
                          (max(0, start-size), end, start))
        return [{"start": a, "end": b, "text": text} for a, b, text in rows]


def candidate_features(path, duration, match=None):
    """Read training candidates from current jobs and original flat JSON checkpoints."""
    analysis = json.loads(path.read_text())
    if analysis.get("format") == FORMAT:
        with reader(path.parent) as db:
            sql = "SELECT start,end,reason,features FROM candidates"
            if match is not None:
                sql += " WHERE start=? AND end=? AND reason=?"
            for a, b, reason, vector in db.execute(sql + " ORDER BY start,end,reason", match or ()):
                yield a, b, reason, json.loads(vector)
    else:
        for row in media.propose(analysis, duration):
            a, b, _, reason = row
            if match is None or (a, b, reason) == match:
                yield a, b, reason, features(analysis, row)
