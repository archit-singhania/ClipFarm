"""Voluntary creator ratings for the original automatic selections."""
import json

from .analysis import candidate_features
from .ranking import FEATURE_VERSION
from .storage import ROOT, db, encode, query

FAMILIES = ("animation", "gaming", "interview", "music", "silent", "sports", "tutorial", "vlog")


def save(clip, body):
    grade = body.get("label")
    rights = body.get("rights", "")
    family = body.get("family", "")
    if clip["parent"] or clip["state"] != "ready":
        raise ValueError("Rate a ready, automatically selected pick.")
    if type(grade) is not int or grade not in range(4) or family not in FAMILIES:
        raise ValueError("Choose a rating and source genre.")
    if not isinstance(rights, str) or not 1 <= len(rights.strip()) <= 250:
        raise ValueError("Record the license or permission for using this source in training.")
    job = query("""SELECT sources.digest,jobs.source FROM sources JOIN jobs ON jobs.source=sources.id
                   WHERE jobs.id=?""", (clip["job"],))[0]
    path = ROOT / clip["job"] / "analysis.json"
    match = (clip["start_ms"], clip["end_ms"], clip["reason"])
    candidate = next(candidate_features(path, json.loads(clip["metadata"])["duration_ms"], match), None)
    if not candidate:
        raise ValueError("These picks use an older selector. Generate a new run to collect training ratings.")
    record = {"source": job["digest"], "duplicate_group": job["digest"], "brief": "general",
              "family": family, "rights": rights.strip(), "label": grade, "target": "human_acceptance",
              "start_ms": candidate[0], "end_ms": candidate[1], "reason": candidate[2],
              "feature_version": FEATURE_VERSION, "features": candidate[3]}
    with db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        other = connection.execute("""SELECT ratings.record FROM ratings JOIN clips ON ratings.clip=clips.id
                                   JOIN jobs ON jobs.id=clips.job WHERE jobs.source=? AND clips.id!=? LIMIT 1""",
                                   (job["source"], clip["id"])).fetchone()
        if other and json.loads(other[0])["family"] != family:
            raise ValueError("Use the same genre as the other ratings for this source.")
        connection.execute("""INSERT INTO ratings(clip,record) VALUES(?,?) ON CONFLICT(clip)
                           DO UPDATE SET record=excluded.record""", (clip["id"], encode(record)))
    return record


def read(clip_id):
    rows = query("SELECT record FROM ratings WHERE clip=?", (clip_id,))
    return json.loads(rows[0]["record"]) if rows else None
