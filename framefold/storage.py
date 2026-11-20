"""Local persistence for sources, runs and immutable clip revisions."""
import contextlib
import json
import os
import sqlite3
import uuid
from pathlib import Path

ROOT = Path(os.environ.get("FRAMEFOLD_DATA", "data")).resolve()
ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL, digest TEXT NOT NULL,
 metadata TEXT NOT NULL, origin TEXT NOT NULL, UNIQUE(owner,digest)
);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, source TEXT NOT NULL REFERENCES sources(id),
 request_key TEXT NOT NULL, settings TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
 stage INTEGER NOT NULL DEFAULT 0, selector TEXT NOT NULL DEFAULT 'rules-1', error TEXT, cancelled INTEGER NOT NULL DEFAULT 0,
 created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(owner,request_key)
);
CREATE TABLE IF NOT EXISTS clips (
 id TEXT PRIMARY KEY, job TEXT NOT NULL REFERENCES jobs(id), rank INTEGER NOT NULL,
 start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL, reason TEXT NOT NULL,
 options TEXT NOT NULL, parent TEXT REFERENCES clips(id), state TEXT NOT NULL DEFAULT 'queued',
 artifact TEXT, error TEXT
);
"""

@contextlib.contextmanager
def db():
    connection = sqlite3.connect(ROOT / "framefold.sqlite", timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def init():
    with db() as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(SCHEMA)
        connection.execute("BEGIN IMMEDIATE")
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(jobs)")}
        if "selector" not in columns:
            connection.execute("ALTER TABLE jobs ADD COLUMN selector TEXT NOT NULL DEFAULT 'rules-1'")


def identifier():
    return uuid.uuid4().hex


def query(sql, args=()):
    with db() as connection:
        return [dict(row) for row in connection.execute(sql, args)]


def execute(sql, args=()):
    with db() as connection:
        connection.execute(sql, args)


def encode(value):
    return json.dumps(value, sort_keys=True)
