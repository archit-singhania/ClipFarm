"""Regression checks for the shipped portable scorer and local review export."""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from framefold.ranking import FEATURES, portable_score, rank
from ml.review import build

MODEL = Path(__file__).resolve().parents[1] / "ml" / "editorial" / "model"


class EditorialModel(unittest.TestCase):
    def test_numeric_split_includes_threshold(self):
        self.assertEqual(portable_score([[0, 2, -1, 1], .25], [2]), -.75)
        self.assertEqual(portable_score([[0, 2, -1, 1], .25], [2.01]), 1.25)

    def test_bundled_ranker_loads_and_per_run_choice_overrides_environment(self):
        metadata = json.loads((MODEL / "model.json").read_text())
        self.assertEqual(tuple(metadata["features"]), FEATURES)
        self.assertEqual(hashlib.sha256((MODEL / "ranker.txt").read_bytes()).hexdigest(),
                         metadata["model_sha256"])
        signals = {"motion": [0,2,8,24,10,20]*8, "energy": [250]*48, "speech": []}
        candidates = [(0,5000,6,"motion"), (5000,13000,5,"energy")]
        with patch.dict(os.environ, {"CLIPFARM_RANKER": "editorial"}):
            native, version = rank(signals, candidates)
            self.assertEqual(version, metadata["model_sha256"])
            self.assertEqual({row for row, _ in native}, set(candidates))
            self.assertEqual(rank(signals, candidates, selector="rules")[1], "rules-2")
        self.assertEqual(rank(signals, candidates, selector="editorial"), (native, version))

    def test_review_escapes_embedded_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "source.mp4"
            first.touch()
            rows = root / "rows.jsonl"
            rows.write_text(json.dumps({"source":"</script><script>body()", "start_ms":0,"end_ms":1000}))
            target = root / "review.html"
            build(rows, first, target)
            page = target.read_text()
            self.assertNotIn("</script><script>body()", page)
            self.assertIn('src="source.mp4"', page)


if __name__ == "__main__":
    unittest.main()
