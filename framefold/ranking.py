"""Versioned feature extraction and optional local model inference."""
import hashlib
import json
import os
import statistics
from pathlib import Path

FEATURE_VERSION = "signals-1"
FEATURES = (
    "rule_score", "duration_seconds", "motion_mean", "motion_peak", "scene_changes",
    "audio_mean", "audio_peak", "audio_available", "speech_fraction", "speech_available",
)


def features(signals, candidate):
    start, end, score, _ = candidate
    lo, hi = start // 500, (end + 499) // 500
    motion = signals["motion"][lo:hi]
    energy = signals["energy"][lo:hi]
    spoken = sum(max(0, min(end, s["end"]) - max(start, s["start"])) for s in signals["speech"])
    return [
        score, (end - start) / 1000, statistics.mean(motion) if motion else 0, max(motion, default=0),
        sum(value >= 20 for value in motion), statistics.mean(energy) if energy else 0,
        max(energy, default=0), int(bool(signals["energy"])), min(1, spoken / (end - start)),
        int(bool(signals["speech"])),
    ]


def portable_score(trees, row):
    """Evaluate finite numeric splits exported from a versioned LightGBM booster."""
    total = 0.0
    for node in trees:
        while isinstance(node, list):
            feature, threshold, left, right = node
            node = left if row[feature] <= threshold else right
        total += node
    return total


def rank(signals, candidates, selector=None):
    """Apply an explicitly configured local ranker or use deterministic baseline scoring."""
    directory = os.environ.get("CLIPFARM_RANKER", os.environ.get("FRAMEFOLD_RANKER"))
    if selector == "editorial":
        directory = "editorial"
    elif selector == "rules":
        directory = None
    elif selector not in (None, "auto"):
        raise ValueError("Choose a supported highlight selector.")
    if directory and candidates:
        try:
            bundled = directory == "editorial"
            path = (Path(__file__).resolve().parents[1] / "ml" / "editorial" / "model"
                    if bundled else Path(directory))
            metadata = json.loads((path / "model.json").read_text())
            model = (path / "ranker.txt").read_bytes()
            digest = hashlib.sha256(model).hexdigest()
            if (metadata["feature_version"] != FEATURE_VERSION or tuple(metadata["features"]) != FEATURES
                    or metadata["model_sha256"] != digest):
                raise ValueError("Model features or checksum do not match the runtime.")
            rows = [features(signals, row) for row in candidates]
            if bundled:
                portable = (path / "trees.json").read_bytes()
                if hashlib.sha256(portable).hexdigest() != metadata["trees_sha256"]:
                    raise ValueError("Portable tree checksum does not match the model.")
                trees = json.loads(portable)
                scores = [portable_score(trees, row) for row in rows]
            else:
                import lightgbm
                import numpy as np
                booster = lightgbm.Booster(model_str=model.decode())
                scores = booster.predict(np.asarray(rows), num_threads=2)
            return sorted(zip(candidates, scores), key=lambda result: (-result[1], result[0][0])), digest
        except (ImportError, OSError, ValueError, KeyError, RuntimeError, TypeError, IndexError) as exc:
            print(f"Using baseline selector: {exc}", flush=True)
    return sorted(((row, row[2]) for row in candidates), key=lambda result: (-result[1], result[0][0])), "rules-2"
