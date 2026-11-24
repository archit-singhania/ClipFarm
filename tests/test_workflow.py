"""Rights-cleared synthetic media through the production HTTP API and worker."""
import array
import hashlib
import math
from concurrent.futures import ThreadPoolExecutor
import http.cookiejar
import io
import json
import os
from pathlib import Path
import random
import signal
import shlex
import sqlite3
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import zipfile


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


class Workflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.env = os.environ | {"FRAMEFOLD_DATA": str(cls.root / "data"), "CLIPFARM_DATA": str(cls.root / "data")}
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            cls.port = listener.getsockname()[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        cls.log = (cls.root / "server.log").open("w+")
        cls.server = subprocess.Popen([sys.executable, "-m", "framefold.server", "--port", str(cls.port),
                                       "--no-worker"], env=cls.env, stdout=cls.log, stderr=cls.log)
        cls.addClassCleanup(cls.finish)
        cls.start_worker()
        for _ in range(80):
            try:
                if cls.read("/api/health")["online"]:
                    return
            except OSError:
                pass
            time.sleep(.1)
        raise RuntimeError("Local app did not start.")

    @classmethod
    def start_worker(cls):
        prefix = shlex.split(os.environ.get("FRAMEFOLD_TEST_WORKER_PREFIX", ""))
        cls.worker = subprocess.Popen([*prefix, sys.executable, "-m", "framefold.worker"], env=cls.env,
                                      stdout=cls.log, stderr=cls.log, start_new_session=True)

    @classmethod
    def stop_worker(cls):
        if cls.worker.poll() is None:
            os.killpg(cls.worker.pid, signal.SIGTERM)
            cls.worker.wait(timeout=5)

    @classmethod
    def finish(cls):
        cls.stop_worker()
        cls.server.terminate()
        cls.server.wait(timeout=5)
        cls.log.seek(0)
        lines = [line for line in cls.log if not line.startswith("127.0.0.1 ")]
        print("".join(lines), end="")
        cls.log.close()
        cls.temporary.cleanup()

    @classmethod
    def request(cls, path, data=None, headers=None):
        return cls.opener.open(urllib.request.Request(cls.base + path, data, headers or {}), timeout=120)

    @classmethod
    def read(cls, path, body=None):
        with cls.request(path, json.dumps(body).encode() if body is not None else None,
                         {"Content-Type": "application/json"}) as response:
            return json.load(response)

    def upload(self, file):
        with self.request("/api/sources", file.read_bytes(),
                          {"Content-Type": "application/octet-stream", "X-File-Name": file.name}) as response:
            return json.load(response)

    def start(self, source, key, options=None):
        request = {"source": source["id"], "key": key, "settings": options or {}}
        job = self.read("/api/jobs", request)
        self.assertEqual(self.read("/api/jobs", request), job)
        return job["id"]

    def wait(self, job, predicate=lambda run: run["state"] not in ("queued", "running"), timeout=180):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.read(f"/api/jobs/{job}")
            if predicate(state):
                return state
            time.sleep(.2)
        self.fail(f"Run timed out: {state}")

    def check_outputs(self, job, captions=False):
        self.assertEqual(job["state"], "ready", job)
        clips = job["clips"]
        self.assertGreaterEqual(len(clips), 2)
        self.assertEqual(len({clip["artifact"]["sha256"] for clip in clips}), len(clips))
        for clip in clips:
            info = clip["artifact"]
            self.assertEqual((info["width"], info["height"], info["codec"]), (1080, 1920, "h264"))
            self.assertEqual(bool(info["captions"]), captions)
            self.assertTrue(info["verified"])
            self.assertEqual(info["audio"], captions)
            if captions:
                lower, upper = info["caption_bounds_ms"]
                self.assertTrue(0 <= lower < upper <= clip["end_ms"] - clip["start_ms"])
                with self.request(f"/api/clips/{clip['id']}/srt") as response:
                    self.assertIn(b"-->", response.read())
            with self.request(f"/api/clips/{clip['id']}/play", headers={"Range": "bytes=0-31"}) as response:
                self.assertEqual(response.status, 206)
                self.assertEqual(len(response.read()), 32)
            with self.request(f"/api/clips/{clip['id']}/download") as response:
                self.assertIn("attachment", response.headers["Content-Disposition"])
                local = self.root / f"{clip['id']}.mp4"
                local.write_bytes(response.read())
            ffmpeg("-i", str(local), "-f", "null", "-")
            print(job["name"], clip["range_label"], info["duration_ms"], info["width"], info["height"],
                  info["codec"], info["bytes"], info["caption_bounds_ms"])
        with self.request(f"/api/jobs/{job['id']}/download") as response:
            bundle = response.read()
            with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
                self.assertEqual(sum(name.endswith(".mp4") for name in archive.namelist()), len(clips))
        with self.request(f"/api/jobs/{job['id']}/download", headers={"Range": "bytes=-256"}) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.read(), bundle[-256:])
        with self.assertRaises(urllib.error.HTTPError) as exception:
            urllib.request.urlopen(f"{self.base}/api/clips/{clips[0]['id']}/download")
        self.assertEqual(exception.exception.code, 404)

    def test_silent_and_revision(self):
        source = self.root / "Silent-events.mp4"
        ffmpeg("-f", "lavfi", "-i", "color=navy:size=320x240:r=15:d=3",
               "-f", "lavfi", "-i", "cellauto=size=320x240:rate=15:rule=110:random_seed=12",
               "-f", "lavfi", "-i", "color=darkgreen:size=320x240:r=15:d=6",
               "-f", "lavfi", "-i", "life=size=320x240:rate=15:random_seed=7",
               "-f", "lavfi", "-i", "color=darkgreen:size=320x240:r=15:d=3",
               "-filter_complex", "[1:v]trim=duration=5[a];[3:v]trim=duration=5[b];[0:v][a][2:v][b][4:v]concat=n=5:v=1:a=0",
               "-pix_fmt", "yuv420p", "-c:v", "libx264", str(source))
        uid = self.start(self.upload(source), "silent")
        self.wait(uid, lambda state: state["stage"] == 4)
        self.stop_worker()
        self.assertFalse(self.read(f"/api/jobs/{uid}")["online"])
        self.start_worker()
        job = self.wait(uid)
        self.check_outputs(job)
        clip = job["clips"][0]
        revision = {"start_ms": clip["start_ms"], "end_ms": clip["start_ms"] + 2000,
                    "settings": {"ratio": "1:1", "quality": "UHD"}}
        endpoint = f"/api/clips/{clip['id']}/revisions"
        copy = self.read(endpoint, revision)
        self.assertEqual(self.read(endpoint, revision), copy)
        updated = self.wait(uid)
        ready = next(item for item in updated["clips"] if item["id"] == copy["id"])
        self.assertEqual(ready["parent"], clip["id"])
        self.assertEqual(ready["state"], "ready", ready)
        self.assertEqual((ready["artifact"]["width"], ready["artifact"]["height"]), (2160, 2160))
        self.assertEqual(next(item for item in updated["clips"] if item["id"] == clip["id"])["artifact"],
                         clip["artifact"])

    def test_follow_subject_and_duck_music(self):
        source = self.root / "Moving-subject.mp4"
        track = self.root / "Music.wav"
        ffmpeg("-f", "lavfi", "-i", "color=black:s=640x360:r=30:d=8", "-f", "lavfi", "-i",
               "color=white:s=90x90:r=30:d=8", "-f", "lavfi", "-i",
               r"aevalsrc=0.65*sin(2*PI*440*t)*between(t\,3\,5):d=8:s=48000",
               "-filter_complex", "[0:v][1:v]overlay=x='65+56*t':y=115[out]", "-map", "[out]",
               "-map", "2:a", "-c:v", "libx264", "-c:a", "aac", str(source))
        ffmpeg("-f", "lavfi", "-i", "sine=f=1700:r=48000:d=1", str(track))
        headers = {"Content-Type": "application/octet-stream", "X-File-Name": track.name}
        with self.request("/api/music", track.read_bytes(), headers) as response:
            music = json.load(response)
        self.assertEqual(music["duration_ms"], 1000)
        source_record = self.upload(source)
        options = {"framing": "track", "music_id": music["id"], "count": 1}
        job = self.wait(self.start(source_record, "follow", options))
        self.assertEqual(job["state"], "ready", job)
        clip = job["clips"][0]
        self.assertEqual(clip["artifact"]["framing_applied"], "track")
        self.assertTrue(clip["artifact"]["soundtrack"])
        # Revise to the full source so measurement timestamps are independent of ranking.
        endpoint = f"/api/clips/{clip['id']}/revisions"
        copy = self.read(endpoint, {"start_ms": 0, "end_ms": 8000})
        self.wait(job["id"])
        local = self.root / "Tracked-mix.mp4"
        with self.request(f"/api/clips/{copy['id']}/download") as response:
            local.write_bytes(response.read())

        def decode(at, *args):
            return subprocess.check_output(["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(local), *args, "-"])

        for at in (.8, 4, 7.1):
            frame = decode(at, "-vf", "scale=54:96,format=gray", "-frames:v", "1", "-f", "rawvideo")
            bright = [i % 54 for i, value in enumerate(frame) if value > 150]
            self.assertGreater(len(bright), 450)
            self.assertAlmostEqual(sum(bright)/len(bright), 27, delta=4)

        def tone_amplitude(at, hz):
            pcm = array.array("f", decode(at, "-map", "0:a", "-t", "0.3", "-ac", "1",
                                           "-ar", "16000", "-f", "f32le"))
            angles = [2*math.pi*hz*i/16000 for i in range(len(pcm))]
            x = sum(v*math.cos(a) for v, a in zip(pcm, angles))
            y = sum(v*math.sin(a) for v, a in zip(pcm, angles))
            return 2*math.hypot(x, y)/len(pcm)

        music_alone = tone_amplitude(2, 1700)
        self.assertGreater(music_alone, .01)
        self.assertLess(tone_amplitude(4, 1700), music_alone*.25)
        self.assertGreater(tone_amplitude(6.5, 1700), music_alone*.8)
        self.assertGreater(tone_amplitude(4, 440), .4)

        plain = self.read(endpoint, {"start_ms": 0, "end_ms": 2000, "settings": {"music_id": None}})
        finished = self.wait(job["id"])["clips"]
        self.assertFalse(next(c for c in finished if c["id"] == plain["id"])["artifact"]["soundtrack"])
        unknown = {"source": source_record["id"], "key": "missing-music", "settings": {"music_id": "0"*32}}
        with self.assertRaises(urllib.error.HTTPError) as missing:
            self.read("/api/jobs", unknown)
        self.assertEqual(missing.exception.code, 404)

    def test_concurrent_create_retry_and_cancellation(self):
        source = self.root / "Concurrent.mp4"
        ffmpeg("-f", "lavfi", "-i", "testsrc2=s=160x90:r=15:d=6", "-c:v", "libx264", str(source))
        imported = self.upload(source)
        self.stop_worker()
        try:
            body = {"source": imported["id"], "key": "concurrent", "settings": {}}
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda _: self.read("/api/jobs", body), range(4)))
            self.assertEqual(len({result["id"] for result in results}), 1)
            uid = results[0]["id"]
            self.assertTrue(self.read(f"/api/jobs/{uid}/cancel", {})["ok"])
            self.assertEqual(self.read(f"/api/jobs/{uid}")["state"], "cancelled")
            for count in (1, 3, 5, 8, 10, 15, 20, 50, 100):
                batch = self.start(imported, f"target-{count}", {"count": count})
                self.assertTrue(self.read(f"/api/jobs/{batch}/cancel", {})["ok"])
                history = next(row for row in self.read("/api/jobs") if row["id"] == batch)
                self.assertEqual((history["requested"], history["ready_originals"]), (count, 0))
            for count in (0, 101, True, 3.5):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    self.start(imported, "invalid-count", {"count": count})
                self.assertEqual(error.exception.code, 400)
        finally:
            self.start_worker()
        run = self.wait(self.start(imported, "retry"))
        self.assertEqual(run["state"], "ready", run)
        clip = run["clips"][0]
        # Place a failed clip at its durable checkpoint to isolate concurrent API retries.
        self.stop_worker()
        try:
            with sqlite3.connect(self.root / "data" / "framefold.sqlite") as connection:
                connection.execute("UPDATE clips SET state='failed', artifact=NULL WHERE id=?", (clip["id"],))
                connection.execute("UPDATE jobs SET state='failed' WHERE id=?", (run["id"],))
            with ThreadPoolExecutor(max_workers=4) as pool:
                retries = list(pool.map(lambda _: self.read(f"/api/clips/{clip['id']}/retry", {}), range(4)))
            self.assertEqual(retries, [{"id": clip["id"]}]*4)
        finally:
            self.start_worker()
        recovered = self.wait(run["id"])
        self.assertEqual(recovered["state"], "ready")
        self.assertEqual(len(recovered["clips"]), len(run["clips"]))
        self.assertEqual(self.read(f"/api/jobs/{uid}")["state"], "cancelled")

    def test_static_and_invalid_link(self):
        source = self.root / "Static.mp4"
        ffmpeg("-f", "lavfi", "-i", "color=sandybrown:s=320x240:d=7", "-c:v", "libx264", str(source))
        job = self.wait(self.start(self.upload(source), "static"))
        self.assertEqual(job["state"], "no_matches")
        self.assertEqual(job["clips"], [])
        with self.assertRaises(urllib.error.HTTPError) as exception:
            self.read("/api/sources", {"url": "https://127.0.0.1/fixture.mp4"})
        self.assertEqual(exception.exception.code, 400)

    @unittest.skipUnless(os.environ.get("CLIPFARM_TEST_LARGE_BATCH"), "opt-in 100-clip HD render")
    def test_two_hour_upload_to_100_distinct_downloads(self):
        # 102 different compositions, each with one motion event. At 2 fps the
        # fixture traverses a real multi-hour timeline with a small encode cost.
        source = self.root / "two-hour-recording.mp4"
        with subprocess.Popen(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "gray",
                               "-s", "32x24", "-r", "2", "-i", "-", "-c:v", "libx264",
                               "-g", "12", "-pix_fmt", "yuv420p", str(source)], stdin=subprocess.PIPE) as proc:
            colors = random.Random(408)
            for _ in range(102):
                rows = [b"".join(bytes([colors.randint(25, 220)]) * 4 for _ in range(8)) for _ in range(6)]
                frame = b"".join(row * 4 for row in rows)
                reverse = bytes(255 - value for value in frame)
                proc.stdin.write(frame * 12 + (reverse + frame) * 3 + frame * 126)
            proc.stdin.close()
            self.assertEqual(proc.wait(), 0)
        # A valid ISO BMFF free-space box crosses the old 1 GiB upload ceiling
        # without requiring an hour of high-bitrate test footage.
        with source.open("ab") as pad:
            box_size = 1024**3
            pad.write(box_size.to_bytes(4, "big") + b"free")
            pad.truncate(pad.tell() + box_size - 8)
        with source.open("rb") as stream, self.request("/api/sources", stream, {
                "Content-Type": "application/octet-stream", "Content-Length": str(source.stat().st_size),
                "X-File-Name": source.name}) as response:
            imported = json.load(response)
        self.assertEqual(imported["duration_ms"], 7_344_000)
        run = self.wait(self.start(imported, "100-clips", {"count": 100}), timeout=2400)
        self.assertEqual(run["state"], "ready", run.get("error"))
        clips = run["clips"]
        self.assertEqual(len(clips), 100)
        self.assertGreater(max(c["start_ms"] for c in clips), 7_000_000)
        self.assertTrue(all(c["artifact"]["verified"] for c in clips))
        self.assertEqual(len({c["artifact"]["sha256"] for c in clips}), 100)
        history = next(row for row in self.read("/api/jobs") if row["id"] == run["id"])
        self.assertEqual((history["ready"], history["ready_originals"], history["requested"]), (100, 100, 100))
        with self.request(f"/api/jobs/{run['id']}/download") as response, tempfile.TemporaryFile() as local:
            while chunk := response.read(1024**2):
                local.write(chunk)
            with zipfile.ZipFile(local) as archive:
                self.assertEqual(len(json.loads(archive.read("manifest.json"))["clips"]), 100)
                for clip in clips:
                    with archive.open(f"pick-{clip['rank']:02}-{clip['id'][:8]}.mp4") as exported:
                        self.assertEqual(hashlib.file_digest(exported, "sha256").hexdigest(), clip["artifact"]["sha256"])

    @unittest.skipUnless(os.environ.get("FRAMEFOLD_WHISPER_MODEL"), "local Whisper model not configured")
    def test_speech(self):
        remote = os.environ.get("FRAMEFOLD_TEST_SPEECH_URL")
        if remote:
            uploaded = self.read("/api/sources", {"url": remote})
        else:
            source = self.root / "Spoken-tutorial.mp4"
            phrases = [
                "Start by filling a glass halfway with cold water. Stir in a full spoon of sugar until the water is clear.",
                "Now add a few drops of food coloring. Watch the color spread through the water and turn the entire glass blue.",
            ]
            audio = []
            for index, phrase in enumerate(phrases):
                path = self.root / f"phrase{index}.aiff"
                subprocess.run(["say", "-r", "165", "-o", str(path), phrase], check=True)
                audio.append(path)
            ffmpeg("-f", "lavfi", "-i", "color=slateblue:s=320x240:r=15:d=30",
                   "-i", str(audio[0]), "-i", str(audio[1]), "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono:d=5",
                   "-filter_complex", "[1:a][3:a][2:a]concat=n=3:v=0:a=1[a]", "-map", "0:v", "-map", "[a]",
                   "-shortest", "-c:v", "libx264", "-c:a", "aac", str(source))
            uploaded = self.upload(source)
        self.check_outputs(self.wait(self.start(uploaded, "speech"), timeout=360), captions=True)


if __name__ == "__main__":
    unittest.main()
