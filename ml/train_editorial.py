"""Train and compare teaser-overlap rankers with one entire film held out per fold."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np

from framefold.ranking import FEATURES, FEATURE_VERSION, portable_score
from ml.ranker import ndcg

PARAMETERS = {"objective": "lambdarank", "learning_rate": .05, "num_leaves": 5,
              "max_depth": 3, "min_data_in_leaf": 8, "reg_lambda": 2,
              "seed": 42, "num_threads": 2, "force_col_wise": True,
              "deterministic": True, "verbosity": -1}
ROUNDS = 30


def train(groups):
    rows = [row for group in groups for row in group]
    data = lgb.Dataset(np.array([r["features"] for r in rows]), label=[r["label"] for r in rows],
                       group=[len(group) for group in groups], feature_name=list(FEATURES))
    return lgb.train(PARAMETERS, data, num_boost_round=ROUNDS)


def metrics(rows, scores):
    order = sorted(zip(rows, scores), key=lambda item: (-item[1], item[0]["start_ms"]))
    top = [row for row, _ in order[:3]]
    return {"ndcg_at_3": ndcg(top, sorted(rows, key=lambda row: -row["label"])),
            "top_3_with_matched_footage": sum(row["overlap_ms"] > 0 for row in top)}


def compact_tree(node):
    if "leaf_value" in node:
        return node["leaf_value"]
    if node["decision_type"] != "<=":
        raise ValueError("The portable runtime accepts only numeric threshold splits.")
    return [node["split_feature"], node["threshold"], compact_tree(node["left_child"]),
            compact_tree(node["right_child"])]


def main(path, output):
    groups = defaultdict(list)
    for line in path.read_text().splitlines():
        row = json.loads(line)
        if row["target"] != "teaser_overlap" or row["feature_version"] != FEATURE_VERSION:
            raise ValueError("This command expects the matching version of teaser-overlap rows.")
        groups[row["duplicate_group"]].append(row)
    if len(groups) < 2:
        raise ValueError("At least two independent films are needed for this comparison.")
    scores = {}
    for held in sorted(groups):
        model = train([rows for group, rows in sorted(groups.items()) if group != held])
        live = [row for row in groups[held] if row["live_candidate"]]
        predicted = model.predict(np.asarray([row["features"] for row in live]), num_threads=2)
        scores[held] = {"generated_candidates": len(live),
                        "candidates_with_matches": sum(row["overlap_ms"] > 0 for row in live),
                        "rules": metrics(live, [row["features"][0] for row in live]),
                        "editorial": metrics(live, predicted)}
    metrics_by_model = {name: float(np.mean([fold[name]["ndcg_at_3"] for fold in scores.values()]))
                        for name in ("rules", "editorial")}
    model = train([rows for _, rows in sorted(groups.items())])
    output.mkdir(parents=True, exist_ok=True)
    model.save_model(str(output / "ranker.txt"))
    sha = hashlib.sha256((output / "ranker.txt").read_bytes()).hexdigest()
    trees = [compact_tree(tree["tree_structure"]) for tree in model.dump_model()["tree_info"]]
    check = [r["features"] for group in groups.values() for r in group]
    if not np.allclose(model.predict(np.asarray(check), num_threads=2),
                       [portable_score(trees, x) for x in check], rtol=1e-12, atol=1e-12):
        raise ValueError("Portable predictions differ from the original model.")
    blob = json.dumps(trees, separators=(",", ":")).encode()
    (output / "trees.json").write_bytes(blob)
    metadata = {"trees_sha256": hashlib.sha256(blob).hexdigest(), "name": "ClipFarm editorial pilot", "target": "teaser_overlap", "feature_version": FEATURE_VERSION,
                "features": FEATURES, "model_sha256": sha, "dataset_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "lightgbm": lgb.__version__, "parameters": PARAMETERS, "rounds": ROUNDS,
                "training_rows": sum(map(len, groups.values())), "held_out_by_film": scores,
                "mean_held_out_ndcg_at_3": metrics_by_model}
    (output / "model.json").write_text(json.dumps(metadata, indent=2, sort_keys=True)+"\n")
    print(json.dumps(scores, indent=2), metrics_by_model)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("labels", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    main(args.labels, args.output)
