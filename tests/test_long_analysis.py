"""Exercise long-run checkpoints, source-relative cues and high pick counts."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from framefold import analysis, media, storage, worker


class LongAnalysis(unittest.TestCase):
    def test_resume_rank_and_read_three_hours_of_regions(self):
        duration = 3 * 3600_000
        seen = []
        def window(source, metadata, directory, start, end):
            self.assertNotIn(start, seen)
            seen.append(start)
            cues = [{"start": t, "end": t+6000, "text": f"Topic {start+t}."}
                    for t in range(60_000, end-start, 60_000)]
            if start == 0:
                cues.append({"start": end-2500, "end": end, "text": "Across the"})
            elif start == media.WINDOW_MS:
                cues.insert(0, {"start": 0, "end": 3500, "text": "boundary."})
            return {"motion": [0]*((end-start)//500), "motion_cues": [0]*((end-start)//500),
                    "fingerprints": [], "energy": [], "speech": cues, "notice": None}

        with tempfile.TemporaryDirectory() as tmp, patch.object(media, "analyze_window", side_effect=window):
            root = Path(tmp)
            def build(cancelled):
                return analysis.build(None, {"duration_ms": duration}, root, "rules", cancelled)
            self.assertIsNone(build(lambda: len(seen) == 4))
            manifest = build(lambda: False)
            self.assertEqual(len(seen), duration//media.WINDOW_MS)
            path = root / "analysis.json"
            path.write_text(json.dumps(manifest))
            self.assertEqual(manifest["selector"], "rules-2")
            for count in (3, 5, 10, 15, 20, 50, 100):
                selected = analysis.picks(root, count)
                self.assertEqual(len(selected), count)
                self.assertEqual(len({p["start_ms"] for p in selected}), count)
            boundary = media.WINDOW_MS
            cues = analysis.cues(root, boundary-1000, boundary+1000)
            self.assertEqual([(s["start"], s["end"]) for s in cues],
                             [(boundary-2500,boundary), (boundary,boundary+3500)])
            cross = next(p for p in selected if p["start_ms"] < boundary < p["end_ms"])
            match = cross["start_ms"], cross["end_ms"], cross["reason"]
            row, = analysis.candidate_features(path, duration, match)
            self.assertEqual(row[:3], match)
            self.assertGreater(row[3][8], .85)
            self.assertGreater(selected[-1]["end_ms"], 2*3600_000)

    def test_late_audio_stream_keeps_its_video_time_in_each_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "delayed.mp4"
            media.command([media.FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=black:s=64x64:r=2:d=18",
                           "-itsoffset", "8", "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
                           "-c:a", "aac", "-c:v", "libx264", str(source)])
            info = media.probe(source)
            for start, end in ((0,18000), (5000,15000), (10000,17500), (14000,18000)):
                signals = media.analyze_window(source, info, root, start, end, transcribe=False)
                active = [start+i*500 for i, rms in enumerate(signals["energy"]) if rms > 220]
                self.assertEqual(active, list(range(max(start,8000), min(end,13000), 500)))

    def test_worker_exports_late_motion_in_two_hour_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(storage, "ROOT", root), patch.object(worker, "ROOT", root):
                storage.init()
                source_id, job_id = storage.identifier(), storage.identifier()
                source = root / (source_id + ".source")
                # Most frames compress to a few bytes. Only the last ten seconds move.
                media.command([media.FFMPEG, "-v", "error", "-f", "lavfi", "-i",
                               "color=black:s=96x64:r=2:d=7200", "-f", "lavfi", "-i",
                               "testsrc2=s=96x64:r=2:d=10", "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0",
                               "-threads", "2", "-g", "60", "-c:v", "libx264", "-f", "mp4", str(source)])
                info = media.probe(source)
                storage.execute("INSERT INTO sources VALUES(?,?,?,?,?,?)", (
                    source_id, "test", "two hours", "fixture", json.dumps(info), "upload"
                ))
                settings = {"ratio": "1:1", "quality": "HD", "count": 100, "framing": "fit", "captions": True}
                storage.execute("INSERT INTO jobs(id,owner,source,request_key,settings) VALUES(?,?,?,?,?)", (
                    job_id, "test", source_id, "long", json.dumps(settings)
                ))
                job, = storage.query("SELECT jobs.*,sources.metadata FROM jobs JOIN sources ON source=sources.id")
                worker.process(job)
                self.assertEqual(storage.query("SELECT state FROM jobs")[0]["state"], "ready")
                clip, = storage.query("SELECT * FROM clips")
                self.assertGreater(clip["start_ms"], 7195_000)
                self.assertLessEqual(clip["end_ms"], info["duration_ms"])
                self.assertTrue(json.loads(clip["artifact"])["verified"])
                self.assertEqual(media.probe(root / job_id / (clip["id"]+".mp4"))["width"], 1080)
