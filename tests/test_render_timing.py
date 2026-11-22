"""Media-level checks for nonzero source seeks, burned cue alignment and quiet footage."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from framefold import media


class RenderTiming(unittest.TestCase):
    def test_late_trim_with_silent_source_soundtrack_and_timed_cue(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = root / "run"
            run.mkdir()
            source = root / "quiet.mp4"
            track = "e" * 32
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=black:s=320x240:r=15:d=8",
                            "-c:v", "libx264", str(source)], check=True)
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=f=1300:d=1",
                            "-f", "wav", str(root / (track + ".music"))], check=True)
            clip = {"start_ms": 4000, "end_ms": 7000, "id": "late",
                    "options": json.dumps({"quality": "HD", "ratio": "9:16", "framing": "track",
                                           "captions": True, "music_id": track})}
            cues = [{"start": 1000, "end": 2000, "text": "Early cue"},
                    {"start": 5500, "end": 6500, "text": "Timed cue"}]
            artifact = media.render(source, media.probe(source), clip, run, cues)
            self.assertEqual(artifact["duration_ms"], 3000)
            self.assertEqual(artifact["caption_bounds_ms"], [1500, 2500])
            self.assertEqual(artifact["framing_applied"], "fit")
            self.assertTrue(artifact["soundtrack"] and artifact["audio"])
            self.assertIn("00:00:01,500 --> 00:00:02,500", (run / "late.srt").read_text())
            self.assertNotIn("Early", (run / "late.vtt").read_text())
            for seek, expected in ((.5, False), (1.8, True), (2.8, False)):
                pixels = media.ffmpeg(run / "late.mp4", "-an", "-frames:v", "1",
                                      "-vf", "scale=270:480,format=gray", "-f", "rawvideo", "-", seek=seek)
                self.assertEqual(any(pixel > 100 for pixel in pixels), expected, seek)
