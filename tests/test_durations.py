import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch

from framefold import analysis, media
from framefold.server import settings


class DurationPreferences(unittest.TestCase):
    def test_validation_and_legacy(self):
        self.assertNotIn("duration", settings({}))
        self.assertEqual(settings({"duration": {"mode":"auto"}}), settings({}))
        for pref in ({"mode":"exact","target_ms":True}, {"mode":"exact","target_ms":300001},
                     {"mode":"range","min_ms":3000,"max_ms":1000}, {"mode":"other"}):
            with self.assertRaises(ValueError):
                settings({"duration":pref})

    def test_boundaries(self):
        signals = {"speech":[{"start":4000,"end":15000,"text":"One complete thought."}],
                   "energy":[],"motion":[]}
        for mode in ("target","exact","range"):
            pref = {"mode":mode,"target_ms":10000} if mode != "range" else {"mode":mode,"min_ms":8000,"max_ms":12000}
            row, = media.propose(signals, 20000, pref)
            self.assertTrue(8000 <= row[1]-row[0] <= 12000)
            if mode == "exact":
                self.assertEqual(row[1]-row[0],10000)
            else:
                self.assertEqual(row[1],15000)
            self.assertEqual(media.propose(signals, 1000, pref), [])

    def test_long_boundary_and_recovery(self):
        def window(source, meta, folder, start, end):
            motion = [9 if 270000 <= t < 330000 else 0 for t in range(start,end,500)]
            return {"motion":motion,"motion_cues":motion,"energy":[],"speech":[],"fingerprints":[],"notice":None}
        with tempfile.TemporaryDirectory() as tmp, patch.object(media,"analyze_window",side_effect=window) as decode:
            pref = {"mode":"exact","target_ms":120000}
            for _ in range(2):
                analysis.build(None, {"duration_ms":600000}, Path(tmp), "rules", lambda:False, pref)
                pick, = analysis.picks(Path(tmp), 1)
                self.assertLess(pick["start_ms"],300000)
                self.assertGreater(pick["end_ms"],300000)
                self.assertEqual(pick["end_ms"]-pick["start_ms"],120000)
            self.assertEqual(decode.call_count,2)
