"""Rights-cleared synthetic media through the production HTTP API and worker."""
import http.cookiejar
import io
import json
import os
from pathlib import Path
import signal
import shlex
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
        cls.env = os.environ | {"FRAMEFOLD_DATA": str(cls.root / "data")}
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
            with zipfile.ZipFile(io.BytesIO(response.read())) as archive:
                self.assertEqual(sum(name.endswith(".mp4") for name in archive.namelist()), len(clips))
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

    def test_static_and_invalid_link(self):
        source = self.root / "Static.mp4"
        ffmpeg("-f", "lavfi", "-i", "color=sandybrown:s=320x240:d=7", "-c:v", "libx264", str(source))
        job = self.wait(self.start(self.upload(source), "static"))
        self.assertEqual(job["state"], "no_matches")
        self.assertEqual(job["clips"], [])
        with self.assertRaises(urllib.error.HTTPError) as exception:
            self.read("/api/sources", {"url": "https://127.0.0.1/fixture.mp4"})
        self.assertEqual(exception.exception.code, 400)

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
