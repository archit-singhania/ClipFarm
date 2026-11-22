"""Prepare weak training labels by locating open-movie teaser footage in each original."""
import argparse
import hashlib
import json
import shutil
import statistics
import tempfile
import zipfile
from pathlib import Path
from urllib.request import urlopen

import numpy as np

from framefold import media
from framefold.ranking import FEATURE_VERSION, features

DATA = Path(__file__).resolve().parent / "editorial"
FPS = 2


def download(url, target, expected):
    if target.is_file() and digest(target) == expected:
        return
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
        staging = Path(temporary.name)
    try:
        with urlopen(url, timeout=60) as source, staging.open("wb") as output:
            shutil.copyfileobj(source, output)
        if digest(staging) != expected:
            raise ValueError(f"Unexpected checksum for {target.name}")
        staging.replace(target)
    finally:
        staging.unlink(missing_ok=True)


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def normalized_frames(path):
    raw = media.ffmpeg(path, "-an", "-vf", "fps=2,scale=48:32,format=gray", "-f", "rawvideo", "-", timeout=600)
    frames = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 32, 48)
    # Remove persistent black bars before comparing different delivery aspect ratios.
    mean = frames.mean(axis=0)
    rows, cols = np.where(mean > 18)
    if not len(rows):
        raise ValueError("No visible frames")
    y = np.linspace(rows.min()+1, rows.max()-1, 16).astype(int)
    x = np.linspace(cols.min()+1, cols.max()-1, 24).astype(int)
    pixels = frames[:, y][:, :, x].astype(np.float32).reshape(len(frames), -1)
    pixels -= pixels.mean(axis=1, keepdims=True)
    magnitude = np.linalg.norm(pixels, axis=1, keepdims=True)
    # Blank frames and fades cannot establish a scene match.
    return np.where(magnitude > 13 * np.sqrt(pixels.shape[1]), pixels / np.maximum(magnitude, 1), 0)


def match(source, trailer):
    similarity = normalized_frames(trailer) @ normalized_frames(source).T
    # Compare consecutive frames with a fixed offset. Looking up individual frames
    # independently makes the match jitter across held poses and repeated shots.
    tracks = (similarity[:-2, :-2] + similarity[1:-1, 1:-1] + similarity[2:, 2:]) / 3
    selected = set()
    for t, scores in enumerate(tracks):
        start = int(scores.argmax())
        if scores[start] < .86 or min(similarity[t+k,start+k] for k in range(3)) < .78:
            continue
        outside = np.r_[scores[:max(0,start-8)], scores[start+9:]]
        if len(outside) and scores[start] - outside.max() < .025:
            continue
        selected.update(range(start, start+3))
    intervals = []
    for frame in sorted(selected):
        if not intervals or frame > intervals[-1][1]:
            intervals.append([frame, frame+1])
        else:
            intervals[-1][1] = frame+1
    return [[a*500,b*500] for a,b in intervals]


def prepare(cache, labels, matches):
    cache.mkdir(parents=True, exist_ok=True)
    records, report = [], {}
    for item in json.loads((DATA / "sources.json").read_text()):
        files = {}
        for role in ("film", "trailer"):
            meta = item[role]
            target = cache / meta["name"]
            download(meta["url"], target, meta["sha256"])
            if meta.get("member"):
                extracted = cache / (item["id"] + "-" + role + "-release" + Path(meta["member"]).suffix)
                with zipfile.ZipFile(target) as archive, archive.open(meta["member"]) as stream:
                    with extracted.open("wb") as output:
                        shutil.copyfileobj(stream, output)
                target = extracted
            files[role] = target
        intervals = match(files["film"], files["trailer"])
        if not intervals:
            raise ValueError(f"No reliable matches for {item['id']}.")
        info = media.probe(files["film"])
        with tempfile.TemporaryDirectory() as tmp:
            signals = media.analyze(files["film"], info, Path(tmp), transcribe=False)
        rows = []
        live = media.propose(signals, info["duration_ms"])
        # Additional windows expose short editorial shots during training. The
        # held-out evaluation uses the same proposal generator as the live app.
        candidates = [(candidate, True) for candidate in live]
        for start in range(0, info["duration_ms"] - 5500, 2000):
            end = start + 5500
            energy = signals["motion"][start//500:end//500]
            score = min(10, statistics.mean(energy) / 2.2) + min(len(energy), 25) / 25
            candidates.append(((start, end, score, "regular window"), False))
        for candidate, live_window in candidates:
            start, end, _, _ = candidate
            overlap = sum(max(0, min(end,b) - max(start,a)) for a,b in intervals)
            fraction = overlap / (end-start)
            grade = 3 if fraction >= .25 else 2 if fraction >= .1 else int(overlap > 0)
            rows.append({"source": item["id"], "duplicate_group": item["id"], "family": "animation",
                         "brief": "trailer-shot", "rights": item["license_url"], "start_ms": start,
                         "end_ms": end, "label": grade, "target": "teaser_overlap", "overlap_ms": overlap, "live_candidate": live_window,
                         "feature_version": FEATURE_VERSION, "features": features(signals,candidate)})
        report[item["id"]] = {"matched_intervals_ms": intervals, "duration_ms":info["duration_ms"],
                              "candidates": len(rows), "graded_counts": [sum(r["label"]==g for r in rows) for g in range(4)]}
        records.extend(rows)
        print(item["id"], report[item["id"]], flush=True)
    labels.write_text("".join(json.dumps(row, sort_keys=True)+"\n" for row in records))
    matches.write_text(json.dumps(report, indent=2, sort_keys=True)+"\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cache", type=Path)
    parser.add_argument("labels", type=Path)
    parser.add_argument("matches", type=Path)
    args = parser.parse_args()
    prepare(args.cache, args.labels, args.matches)
