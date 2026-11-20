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


def rank(signals, candidates):
    """Always fall back to rules when a local model is absent or unusable."""
    directory = os.environ.get("FRAMEFOLD_RANKER")
    if directory and candidates:
        try:
            import lightgbm
            import numpy as np
            path = Path(directory)
            metadata = json.loads((path / "model.json").read_text())
            model = (path / "ranker.txt").read_text()
            digest = hashlib.sha256(model.encode()).hexdigest()
            if (metadata["feature_version"] != FEATURE_VERSION or tuple(metadata["features"]) != FEATURES
                    or metadata["model_sha256"] != digest):
                raise ValueError("Model features or checksum do not match the runtime.")
            booster = lightgbm.Booster(model_str=model)
            scores = booster.predict(np.asarray([features(signals, row) for row in candidates]), num_threads=2)
            return sorted(zip(candidates, scores), key=lambda result: (-result[1], result[0][0])), digest
        except (ImportError, OSError, ValueError, KeyError, RuntimeError) as exc:
            print(f"Using baseline selector: {exc}", flush=True)
    return sorted(((row, row[2]) for row in candidates), key=lambda result: (-result[1], result[0][0])), "rules-1"
