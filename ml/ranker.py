"""Prepare labels from local analysis and train a grouped LightGBM ranker."""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import random

from framefold.analysis import candidate_features
from framefold.ranking import FEATURES, FEATURE_VERSION

SEED = 42
FAMILIES = {"interview", "sports", "gaming", "tutorial", "music", "silent", "vlog", "animation"}


def export(args):
    with args.output.open("w") as output:
        for start, end, reason, vector in candidate_features(args.analysis, args.duration_ms):
            row = {"source": args.source, "duplicate_group": args.duplicate_group,
                   "family": args.family, "brief": args.brief, "rights": args.rights,
                   "start_ms": start, "end_ms": end, "reason": reason, "label": None,
                   "feature_version": FEATURE_VERSION, "features": vector}
            output.write(json.dumps(row)+"\n")


def grouped_rows(path):
    groups = defaultdict(list)
    properties = {}
    for line in path.read_text().splitlines():
        row = json.loads(line)
        if row.get("target", "human_acceptance") != "human_acceptance":
            raise ValueError("The creator-acceptance model requires direct human ratings.")
        if (not row["rights"] or row["family"] not in FAMILIES or type(row["label"]) is not int
                or not 0 <= row["label"] <= 3 or row["feature_version"] != FEATURE_VERSION
                or len(row["features"]) != len(FEATURES)
                or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in row["features"])
                or not 0 <= row["start_ms"] < row["end_ms"] or not row["duplicate_group"]):
            raise ValueError("Invalid candidate label or manifest metadata.")
        group = row["duplicate_group"]
        previous = properties.setdefault(row["source"], (group, row["family"]))
        if previous != (group, row["family"]):
            raise ValueError("Assign each original a consistent duplicate group and family.")
        groups[(group, row["source"], row["brief"])].append(row)
    if not groups:
        raise ValueError("Provide labeled candidates from independent originals.")
    families = defaultdict(set)
    for group, source, _ in groups:
        families[properties[source][1]].add(group)
    covered = set()
    plan = {}
    rng = random.Random(SEED)
    for family, members in sorted(families.items()):
        if covered & members:
            raise ValueError(f"A duplicate group crosses genre families near {family}.")
        covered.update(members)
        members = sorted(members)
        if len(members) < 3:
            raise ValueError(f"{family} needs at least three independent source groups.")
        rng.shuffle(members)
        test = max(1, round(len(members) / 3))
        validation = max(1, round(len(members) * 2 / 15))
        for index, group in enumerate(members):
            plan[group] = "test" if index < test else "validation" if index < test+validation else "train"
    return {split: [groups[key] for key in sorted(groups) if plan[key[0]] == split]
            for split in ("train", "validation", "test")}, plan


def ndcg(order, ideal):
    def dcg(rows):
        return sum((2 ** row["label"]-1) / math.log2(i+2) for i, row in enumerate(rows[:3]))
    return dcg(order) / dcg(ideal) if dcg(ideal) else 0


def evaluate(groups, scorer):
    by_family = defaultdict(list)
    for rows in groups:
        scores = scorer(rows)
        ordered = sorted(zip(rows, scores), key=lambda item: (-item[1], item[0]["start_ms"]))
        top = [row for row, _ in ordered][:3]
        ideal = sorted(rows, key=lambda row: row["label"], reverse=True)
        metrics = (ndcg(top, ideal), sum(row["label"] >= 2 for row in top), len(top),
                   int(any(row["label"] >= 2 for row in top)))
        for key in set(("all", rows[0]["family"])):
            by_family[key].append(metrics)
    return {key: {"queries": len(items), "ndcg_at_3": sum(i[0] for i in items) / len(items),
                  "human_acceptance_at_3": sum(i[1] for i in items) / sum(i[2] for i in items),
                  "queries_with_accepted_pick": sum(i[3] for i in items)}
            for key, items in by_family.items()}


def train(args):
    import lightgbm as lgb
    import numpy as np
    splits, plan = grouped_rows(args.labels)

    def dataset(name):
        groups = splits[name]
        rows = [row for group in groups for row in group]
        return lgb.Dataset(np.asarray([row["features"] for row in rows]), label=[row["label"] for row in rows],
                           group=[len(group) for group in groups], feature_name=list(FEATURES))

    training = dataset("train")
    validation = dataset("validation")
    validation.set_reference(training)
    model = lgb.train({"objective": "lambdarank", "metric": "ndcg", "ndcg_eval_at": [3],
                       "seed": SEED, "deterministic": True, "force_col_wise": True,
                       "verbosity": -1, "num_threads": 2},
                      training, num_boost_round=200, valid_sets=[validation],
                      callbacks=[lgb.early_stopping(20, verbose=False)])
    args.output.mkdir(parents=True, exist_ok=True)
    artifact = args.output / "ranker.txt"
    model.save_model(str(artifact))
    metadata = {"feature_version": FEATURE_VERSION, "features": FEATURES, "split_seed": SEED,
                "model_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                "dataset_sha256": hashlib.sha256(args.labels.read_bytes()).hexdigest(),
                "splits": plan, "lightgbm": lgb.__version__,
                "baseline": evaluate(splits["test"], lambda rows: [row["features"][0] for row in rows]),
                "ranker": evaluate(splits["test"], lambda rows: model.predict(
                    np.asarray([row["features"] for row in rows]), num_threads=2))}
    (args.output / "model.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: metadata[key] for key in ("baseline", "ranker")}, indent=2))


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("candidates")
    prepare.add_argument("analysis", type=Path)
    prepare.add_argument("output", type=Path)
    prepare.add_argument("--duration-ms", required=True, type=int)
    prepare.add_argument("--source", required=True)
    prepare.add_argument("--duplicate-group", required=True)
    prepare.add_argument("--family", required=True, choices=sorted(FAMILIES))
    prepare.add_argument("--brief", default="general")
    prepare.add_argument("--rights", required=True)
    fit = commands.add_parser("train")
    fit.add_argument("labels", type=Path)
    fit.add_argument("output", type=Path)
    args = parser.parse_args()
    (export if args.command == "candidates" else train)(args)


if __name__ == "__main__":
    main()
