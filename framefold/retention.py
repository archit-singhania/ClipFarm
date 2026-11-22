"""Expire terminal runs when the operator enables a retention period."""
import os
import re
import shutil
from datetime import datetime, timedelta, timezone

from .storage import ROOT, db

TERMINAL = ("ready", "partial", "failed", "no_matches", "cancelled")
IDENTIFIER = re.compile(r"[0-9a-f]{32}")


def sweep():
    """Run in the exclusive, idle local worker. Keep files until the DB commits."""
    value = os.environ.get("CLIPFARM_RETENTION_DAYS")
    if not value:
        return
    try:
        days = int(value)
        if not 1 <= days <= 36500:
            raise ValueError
    except ValueError:
        print("CLIPFARM_RETENTION_DAYS must be a whole number from 1 to 36500.", flush=True)
        return
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("CREATE TABLE IF NOT EXISTS expired_files (name TEXT PRIMARY KEY)")
        placeholders = ",".join("?" for _ in TERMINAL)
        expired = connection.execute(
            f"SELECT id, source FROM jobs WHERE created < ? AND state IN ({placeholders})",
            (cutoff, *TERMINAL),
        ).fetchall()
        for job in expired:
            connection.execute("DELETE FROM clips WHERE job=?", (job["id"],))
            connection.execute("DELETE FROM jobs WHERE id=?", (job["id"],))
            connection.execute("INSERT OR IGNORE INTO expired_files VALUES (?)", (job["id"],))
            removed = connection.execute(
                "DELETE FROM sources WHERE id=? AND NOT EXISTS (SELECT 1 FROM jobs WHERE source=?)",
                (job["source"], job["source"]),
            )
            if removed.rowcount:
                connection.execute("INSERT OR IGNORE INTO expired_files VALUES (?)", (job["source"] + ".source",))
        music = connection.execute("""
            SELECT id FROM music WHERE created < ?
            AND NOT EXISTS (SELECT 1 FROM jobs WHERE json_extract(settings, '$.music_id')=music.id)
            AND NOT EXISTS (SELECT 1 FROM clips WHERE json_extract(options, '$.music_id')=music.id)
        """, (cutoff,)).fetchall()
        for track in music:
            connection.execute("DELETE FROM music WHERE id=?", (track["id"],))
            connection.execute("INSERT OR IGNORE INTO expired_files VALUES (?)", (track["id"] + ".music",))
        pending = connection.execute("SELECT name FROM expired_files").fetchall()
    for row in pending:
        name = row["name"]
        if not IDENTIFIER.fullmatch(name.removesuffix(".source").removesuffix(".music")):
            continue
        path = ROOT / name
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        except OSError as exc:
            print(f"Retention cleanup will retry {name}: {exc}", flush=True)
            continue
        with db() as connection:
            connection.execute("DELETE FROM expired_files WHERE name=?", (name,))
