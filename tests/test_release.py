"""Release gates for recovery, framing, import validation and opted-in retention."""
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from framefold import imports, media, retention, storage, worker
from framefold.server import DEFAULTS


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", *map(str, args)], check=True)


class ReleaseMatrix(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for module in (storage, worker, retention, imports):
            overlay = patch.object(module, "ROOT", self.root)
            overlay.start()
            self.addCleanup(overlay.stop)
        storage.init()

    def source(self):
        source = self.root / "fixture.mp4"
        ffmpeg("-f", "lavfi", "-i", "testsrc2=size=160x90:rate=20:duration=4",
               "-pix_fmt", "yuv420p", source)
        return source

    def test_all_aspects_and_resolutions_with_rotation_and_vfr(self):
        varying = self.root / "varying.mp4"
        ffmpeg("-i", self.source(), "-vf", "select='lt(t,2)+not(mod(n,3))'", "-fps_mode", "vfr", varying)
        rotated = self.root / "portrait.mp4"
        ffmpeg("-display_rotation", "90", "-i", varying, "-c", "copy", rotated)
        info = media.probe(rotated)
        self.assertEqual((info["width"], info["height"]), (90,160))
        for ratio, dimensions in media.PRESETS.items():
            for quality in ("HD", "UHD"):
                with self.subTest(ratio=ratio, quality=quality):
                    opts = DEFAULTS | {"ratio": ratio, "quality": quality, "framing": "crop"}
                    clip = {"id": storage.identifier(), "options": json.dumps(opts),
                            "start_ms": 1200, "end_ms": 2400}
                    result = media.render(rotated, info, clip, self.root, [])
                    factor = 2 if quality == "UHD" else 1
                    self.assertEqual((result["width"], result["height"]), tuple(v*factor for v in dimensions))
                    self.assertTrue(result["upscale"])
                    self.assertLessEqual(abs(result["duration_ms"] - 1200), 100)
                    ffmpeg("-i", self.root / (clip["id"]+".mp4"), "-f", "null", "-")

    def test_invalid_media_and_interrupted_transfer(self):
        for stream, size in ((b"corrupt", 7), (b"truncated", 100)):
            with self.assertRaises(ValueError):
                imports.save(io.BytesIO(stream), size, "fixture.mp4", "upload", "owner")
            self.assertFalse(list(self.root.glob("*.upload")))
        audio = self.root / "audio.mp4"
        ffmpeg("-f", "lavfi", "-i", "sine=d=3", "-c:a", "aac", audio)
        with audio.open("rb") as stream, self.assertRaisesRegex(ValueError, "video"):
            imports.save(stream, audio.stat().st_size, "audio.mp4", "upload", "owner")
        self.assertFalse(storage.query("SELECT * FROM sources"))

    def test_partial_render_on_full_disk_and_retry(self):
        path = self.source()
        with path.open("rb") as stream:
            src = imports.save(stream, path.stat().st_size, "fixture.mp4", "upload", "owner")
        job = storage.identifier()
        storage.execute("""INSERT INTO jobs(id,owner,source,request_key,settings,stage)
                         VALUES (?,?,?,?,?,3)""", (job, "owner",src["id"],"test",json.dumps(DEFAULTS)))
        folder = self.root / job
        folder.mkdir()
        (folder / "analysis.json").write_text('{"speech":[]}')
        ids = [storage.identifier(), storage.identifier()]
        for i, uid in enumerate(ids, 1):
            storage.execute("""INSERT INTO clips(id,job,rank,start_ms,end_ms,reason,options)
                            VALUES(?,?,?,?,?,?,?)""", (uid,job,i,(i-1)*1500,i*1500,"fixture",json.dumps(DEFAULTS)))
        def process():
            row = storage.query("SELECT jobs.*,sources.metadata FROM jobs JOIN sources ON source=sources.id")[0]
            worker.process(row)
        with patch.object(worker.shutil, "disk_usage", side_effect=[
            SimpleNamespace(free=1024**3), SimpleNamespace(free=0)
        ]):
            process()
        files = storage.query("SELECT * FROM clips ORDER BY rank")
        self.assertEqual([r["state"] for r in files], ["ready","failed"])
        self.assertEqual(storage.query("SELECT state FROM jobs")[0]["state"], "partial")
        storage.execute("UPDATE clips SET state='queued' WHERE id=?", (ids[1],))
        process()
        self.assertEqual(storage.query("SELECT state FROM jobs")[0]["state"], "ready")
        self.assertEqual(storage.query("SELECT artifact FROM clips WHERE id=?", (ids[0],))[0]["artifact"],
                         files[0]["artifact"])

    def test_retention_respects_shared_sources_and_queued_work(self):
        source, old, current = [storage.identifier() for _ in range(3)]
        storage.execute("INSERT INTO sources VALUES (?,?,?,?,?,?)", (source,"owner","test","hash","{}","upload"))
        original = self.root / (source+".source")
        original.touch()
        for uid, state in ((old, "ready"), (current, "queued")):
            storage.execute("""INSERT INTO jobs(id,owner,source,request_key,settings,state,created)
                             VALUES(?,?,?,?,?,?,?)""", (uid,"owner",source,uid,"{}",state,"2020-01-01"))
            (self.root / uid).mkdir()
        with patch.dict(os.environ, {"CLIPFARM_RETENTION_DAYS":"14"}):
            retention.sweep()
            self.assertFalse((self.root / old).exists())
            self.assertTrue(original.exists())
            self.assertTrue((self.root / current).is_dir())
            storage.execute("UPDATE jobs SET state='cancelled' WHERE id=?", (current,))
            retention.sweep()
            retention.sweep()
            self.assertFalse(original.exists())
            self.assertFalse(storage.query("SELECT id FROM sources"))
