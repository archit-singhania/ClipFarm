"""Single local worker with crash-safe stage and per-clip checkpoints."""
import fcntl
import json
import os
import shutil
import time

from . import media
from .storage import ROOT, db, encode, execute, identifier, init, query

ACTIVE = ("queued", "running")


def directory(job):
    path = ROOT / job["id"]
    path.mkdir(mode=0o700, exist_ok=True)
    return path


def ready_notice(job):
    path = ROOT / job["id"] / "analysis.json"
    try:
        return json.loads(path.read_text()).get("notice")
    except FileNotFoundError:
        return None


def set_stage(job, stage):
    execute("UPDATE jobs SET state='running',stage=? WHERE id=?", (stage, job["id"]))


def cancelled(job):
    return query("SELECT cancelled FROM jobs WHERE id=?", (job["id"],))[0]["cancelled"]


def process(job):
    job_id = job["id"]
    source = ROOT / (job["source"] + ".source")
    info = json.loads(job["metadata"])
    settings = json.loads(job["settings"])
    path = directory(job)
    try:
        if cancelled(job):
            return
        if job["stage"] < 2:
            set_stage(job, 1)
            preview = path / "preview.jpg"
            if not preview.is_file():
                temp = path / "preview.tmp.jpg"
                media.ffmpeg(source, "-map", f"0:{info['video_index']}", "-frames:v", "1",
                             "-vf", "scale=720:-2", str(temp))
                temp.replace(preview)
            set_stage(job, 2)
        if cancelled(job):
            return
        analysis = path / "analysis.json"
        if not analysis.exists():
            result = media.analyze(source, info, path)
            temporary = analysis.with_suffix(".tmp")
            temporary.write_text(encode(result))
            temporary.replace(analysis)
        else:
            result = json.loads(analysis.read_text())
        if cancelled(job):
            return
        if job["stage"] < 3:
            picks, selector = media.select(result, info["duration_ms"], settings["count"])
            with db() as connection:
                connection.executemany(
                    "INSERT INTO clips (id,job,rank,start_ms,end_ms,reason,options) VALUES (?,?,?,?,?,?,?)",
                    [(identifier(), job_id, i, pick["start_ms"], pick["end_ms"], pick["reason"], job["settings"])
                     for i, pick in enumerate(picks, 1)],
                )
                connection.execute("UPDATE jobs SET stage=3,selector=? WHERE id=?", (selector, job_id))
        if cancelled(job):
            return
        clips = query("SELECT * FROM clips WHERE job=? AND state='queued' ORDER BY rank", (job_id,))
        set_stage(job, 4)
        for clip in clips:
            if cancelled(job):
                return
            try:
                if shutil.disk_usage(ROOT).free < 512 * 1024**2:
                    raise ValueError("Free at least 512 MB of disk space and retry.")
                artifact = media.render(source, info, clip, path, result["speech"])
                execute("UPDATE clips SET state='ready',artifact=?,error=NULL WHERE id=?",
                        (encode(artifact), clip["id"]))
            except (ValueError, OSError) as exc:
                print(f"Clip {clip['id']}: {exc}", flush=True)
                execute("UPDATE clips SET state='failed',error=? WHERE id=?",
                        (public_error(exc, "Video export failed. Check FFmpeg and retry."), clip["id"]))
    except (ValueError, OSError) as exc:
        print(f"Run {job_id}: {exc}", flush=True)
        execute("UPDATE jobs SET state='failed',error=? WHERE id=?",
                (public_error(exc, "Could not process this video. Try a different source."), job_id))
        return
    finally:
        if cancelled(job):
            execute("UPDATE jobs SET state='cancelled' WHERE id=?", (job_id,))
    with db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        state = connection.execute(
            "SELECT count(*) total, count(CASE WHEN state='ready' THEN 1 END) ready,"
            " count(CASE WHEN state='failed' THEN 1 END) failed,"
            " count(CASE WHEN state='queued' THEN 1 END) queued FROM clips WHERE job=?", (job_id,)
        ).fetchone()
        # Revisions or retries can arrive while another clip is rendering.
        next_state = ("queued" if state["queued"] else "no_matches" if not state["total"] else
                      "partial" if state["ready"] and state["failed"] else "ready" if state["ready"] else "failed")
        connection.execute("UPDATE jobs SET state=CASE WHEN cancelled THEN 'cancelled' ELSE ? END WHERE id=?",
                           (next_state, job_id))


def public_error(exc, default):
    message = str(exc)
    return message if isinstance(exc, ValueError) and not message.startswith("Media processing failed.") else default


def serve():
    init()
    with (ROOT / "worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("The worker is already running for this data directory.")
        for temporary in ROOT.glob("*/*.tmp.mp4"):
            temporary.unlink(missing_ok=True)
        while True:
            jobs = query("""SELECT jobs.*,sources.metadata FROM jobs JOIN sources ON sources.id=jobs.source
                          WHERE jobs.state IN ('queued','running') ORDER BY jobs.created LIMIT 1""")
            if jobs:
                process(jobs[0])
            else:
                time.sleep(0.6)


def online():
    with (ROOT / "worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            return True


if __name__ == "__main__":
    os.umask(0o077)
    serve()
