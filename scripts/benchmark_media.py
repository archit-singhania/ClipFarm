"""Build six original fixtures and benchmark the two upstream stages and local HTTP worker.

Run from the project root with the CLI examples in docs/media-benchmark.md.
"""
import argparse
import hashlib
import http.cookiejar
import importlib.util
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import wave

from PIL import Image, ImageDraw, ImageFont

PINS = {"openshorts": "b3cd947bc48ad1fc8093bb8f8291b9fb02e9bb2f",
        "clipping": "1f4e5bcf90f1edf7c1478ca52a683abdb07a5aa7"}
CHAPTERS = [
    ("Source", "Start with an original recording. Play the opening minute and listen for a complete idea you can explain without the rest of the video."),
    ("Mark in", "Move the playhead to the beginning of that idea. Leave enough room for a viewer to understand who is speaking and what they are describing."),
    ("Mark out", "Watch the action all the way through its result. Place the end marker after the result so that the shot includes its own conclusion."),
    ("Compare", "Compare this moment with the other highlights. Two shots of the same event give your audience very little extra information."),
    ("Frame", "Choose a frame shape that fits the destination. Check where the subject moves before you make a narrow crop."),
    ("Track", "Follow the moving subject across the shot. When the camera changes angle, check that the face or object still fits inside the frame."),
    ("Listen", "Play the source sound on its own. Listen through a pause and through the loudest line before setting the music level."),
    ("Mix", "Add a quiet music track underneath the voice. Let the music become quieter during the sentence and return gently in the space after it."),
    ("Caption", "Read the subtitles aloud as the scene plays. The first word should appear when the speaker begins and disappear after the phrase ends."),
    ("Trim", "Watch the clip again from its very first frame. Adjust either edge only if the opening or ending feels incomplete."),
    ("Export", "Save a copy in the size you selected. Inspect its image dimensions, listen to the audio and check that captions line up with the source."),
    ("Review", "Finally play the exported file from beginning to end. Keep the original and its source range so that another editor can reproduce the same cut."),
]
# Hand-specified landmarks, independent of the ranker. Only these samples are benchmarked.
FIXTURES = {"tutorial": (50, 56), "craft": (2, 6), "sport": (1, 5),
            "screencast": (2, 6), "music": (2, 6), "mixed": (2, 6)}


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args)], check=True)


def probe(path):
    return json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_format", "-show_streams",
                                               "-of", "json", str(path)]))


def tutorial(root):
    playlist = []
    with wave.open(str(root / "narration.wav"), "wb") as narration:
        narration.setparams((1, 2, 16000, 0, "NONE", ""))
        for index, (title, phrase) in enumerate(CHAPTERS):
            subprocess.run(["say", "-r", "150", "-o", str(root / "voice.aiff"), phrase], check=True)
            ffmpeg("-i", root / "voice.aiff", "-ar", "16000", "-ac", "1", root / "phrase.wav")
            with wave.open(str(root / "phrase.wav"), "rb") as sound:
                buf = sound.readframes(sound.getnframes())
            if len(buf) < 2 * 16000:
                raise RuntimeError("The system voice did not generate audio. Run with local speech services available.")
            narration.writeframes(buf + bytes(50 * 16000 * 2 - len(buf)))
            board = Image.new("RGB", (1920, 1080), "#16291e")
            pen = ImageDraw.Draw(board)
            pen.text((110, 100), "A FIELD GUIDE TO VIDEO EDITING", font=ImageFont.load_default(45), fill="#ecfa86")
            pen.text((100, 360), title, font=ImageFont.load_default(190), fill="white")
            for step in range(len(CHAPTERS)):
                x = 110 + step*143
                pen.rectangle((x, 900, x+120, 924), fill="#e8fb72" if step <= index else "#52605a")
            name = f"chapter-{index:02}.png"
            board.save(root / name)
            playlist.extend((f"file '{name}'", "duration 50"))
        playlist.append(f"file '{name}'")
    (root / "boards.txt").write_text("\n".join(playlist))
    ffmpeg("-f", "concat", "-safe", "1", "-i", root / "boards.txt", "-i", root / "narration.wav", "-t", "600",
           "-vf", "fps=24,format=yuv420p", "-c:v", "libx264", "-threads", "4", "-preset", "ultrafast",
           "-c:a", "aac", "-movflags", "+faststart", root / "tutorial.mp4")


def animated(root, name):
    size = (3840, 2160) if name == "sport" else (640, 360)
    fps = 8
    silent = root / "motion.mp4"
    process = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                                "-s", f"{size[0]}x{size[1]}", "-r", str(fps), "-i", "-", "-c:v", "libx264",
                                "-threads", "4", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(silent)],
                               stdin=subprocess.PIPE)
    try:
        for frame in range(16*fps):
            t = frame / fps
            # Two four-second demonstrations with still holds between them.
            step = min(1, max(0, (t-2)/4))
            second = min(1, max(0, (t-10)/4))
            width, height = size
            board = Image.new("RGB", size, "#213b32")
            pen = ImageDraw.Draw(board)
            if name == "sport":
                pen.rectangle((80, 80, width-80, height-80), outline="#d6efd4", width=22)
                pen.line((width//2, 80, width//2, height-80), fill="#d6efd4", width=22)
                x = int(width*(.12+.73*(step-second)))
                y = int(height*(.46-.25*math.sin((step+second)*math.pi)))
                radius = width//16
                pen.ellipse((x-radius, y-radius, x+radius, y+radius), fill="#fef1a3", outline="#2f3127", width=18)
            elif name == "craft":
                # A two-fold paper animation and the visible final folded shape.
                x, y = int(80 + 250*step), int(260-170*second)
                pen.polygon(((80, 60), (550, 60), (550, y), (x, 284)), fill="#dfaf8f")
                pen.polygon(((80, 60), (x, 284), (330, 60)), fill="#9d5d44")
            elif name in ("screencast", "mixed"):
                pen.rounded_rectangle((45, 36, 594, 318), radius=12, fill="#d7e4d0")
                pen.text((75, 67), "Edit timeline", fill="#16231d", font=ImageFont.load_default(28))
                for i in range(8):
                    x = 80+i*56
                    pen.rectangle((x, 192, x+48, 255), fill="#718aa3" if i > 4 else "#bb7656")
                pen.rectangle((79+int(190*step), 174, 535-int(100*second), 265), outline="#203342", width=8)
            else:
                for i in range(8):
                    active = 2 <= t < 6 or 10 <= t < 14
                    h = 50+int(120*abs(math.sin(i+.75*frame))) if active else 50
                    pen.rectangle((60+65*i, 290-h, 102+65*i, 290), fill="#cddf86")
            process.stdin.write(board.tobytes())
    finally:
        process.stdin.close()
        if process.wait():
            raise RuntimeError(f"Fixture encoding failed for {name}.")
    output = root / (name + ".mp4")
    if name == "music":
        tone = r"aevalsrc=0.2*sin(2*PI*330*t)*(between(t\,2\,6)+between(t\,10\,14)):d=16:s=44100"
        ffmpeg("-i", silent, "-f", "lavfi", "-i", tone, "-c:v", "copy", "-c:a", "aac", output)
    elif name == "mixed":
        ffmpeg("-i", silent, "-i", root / "phrase.wav", "-map", "0:v", "-map", "1:a", "-af", "apad",
               "-t", "16", "-c:v", "copy", "-c:a", "aac", output)
    else:
        silent.replace(output)


def load_stage(repo, relative, pin):
    revision = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    if revision != pin:
        raise ValueError(f"Expected {pin} in {repo}.")
    path = repo / relative
    module = importlib.util.spec_from_file_location(repo.name.replace("-", "_"), path)
    stage = importlib.util.module_from_spec(module)
    module.loader.exec_module(stage)
    return stage


def audit(root, openshorts, clipping):
    cuts = load_stage(openshorts, "ffmpeg_utils.py", PINS["openshorts"])
    encode = load_stage(clipping, "clipping/studio/ffmpeg_utils.py", PINS["clipping"])
    os.environ["FFMPEG_ENCODER"] = "x264"
    encoder = encode.detect_video_encoder(target_h=720)
    result = {}
    for name, (start, end) in FIXTURES.items():
        source = root / (name + ".mp4")
        entry = {"source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "range_s": [start, end]}
        for method in ("openshorts", "clipping"):
            output = root / f"{name}-{method}.mp4"
            before = time.monotonic()
            if method == "openshorts":
                cuts.cut_clip(str(source), str(output), start, end, 1)
            else:
                cmd = ["ffmpeg", "-y", "-ss", str(start), "-i", str(source), "-t", str(end-start),
                       "-map", "0:v:0", "-map", "0:a:0?", "-c:a", "aac", "-threads", "4",
                       *encode.get_mp4_encode_args(encoder, fps=24)]
                code, stderr = encode.run_ffmpeg_with_progress(encode.build_ffmpeg_progress_cmd(cmd, str(output)),
                                                             end-start, name)
                if code:
                    raise RuntimeError(stderr)
            elapsed = time.monotonic()-before
            info = probe(output)
            ffmpeg("-i", output, "-f", "null", "-")
            entry[method] = {"seconds": round(elapsed, 3), "bytes": output.stat().st_size,
                             "duration_s": float(info["format"]["duration"]),
                             "tracks": [s["codec_name"] for s in info["streams"]]}
        result[name] = entry
    (root / "upstream.json").write_text(json.dumps(result, indent=2))


def process_rss(root_pid):
    processes = subprocess.check_output(["ps", "-axo", "pid=,ppid=,rss="], text=True)
    tree = {}
    for row in processes.splitlines():
        pid, parent, rss = map(int, row.split())
        tree.setdefault(parent, []).append((pid, rss))
    def descendants(pid):
        return sum(rss + descendants(child) for child, rss in tree.get(pid, []))
    return descendants(root_pid) + sum(rss for entries in tree.values() for pid, rss in entries if pid == root_pid)


def measure(root, model):
    state = root / "private"
    state.mkdir(exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = subprocess.Popen([sys.executable, "-m", "framefold.server", "--port", str(port)],
                              env=os.environ | {"FRAMEFOLD_DATA": str(state), "FRAMEFOLD_WHISPER_MODEL": str(model)},
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def call(path, value=None, headers=None):
        request = urllib.request.Request(f"http://127.0.0.1:{port}/api/{path}", data=value,
                                         headers=headers or {"Content-Type":"application/json"})
        with client.open(request, timeout=120) as response:
            return json.load(response)
    result = {}
    try:
        for _ in range(100):
            try:
                if call("health")["online"]:
                    break
            except OSError:
                pass
            time.sleep(.1)
        for name in FIXTURES:
            settings = {"ratio": "16:9", "quality": "UHD" if name == "sport" else "HD", "count": 3}
            def disk():
                sizes = []
                for path in state.rglob("*"):
                    try:
                        if path.is_file():
                            sizes.append(path.stat().st_size)
                    except FileNotFoundError:
                        pass
                return sum(sizes)
            initial = disk()
            start = time.monotonic()
            maxima = [0, 0]
            stop = threading.Event()
            def sample():
                while not stop.is_set():
                    maxima[0] = max(maxima[0], process_rss(server.pid))
                    maxima[1] = max(maxima[1], disk()-initial)
                    stop.wait(.25)
            monitor = threading.Thread(target=sample)
            monitor.start()
            try:
                path = root / (name + ".mp4")
                source = call("sources", path.read_bytes(), {"Content-Type":"application/octet-stream",
                                                             "X-File-Name":path.name})
                job = call("jobs", json.dumps({"source":source["id"],"settings":settings,"key":name}).encode())
                first = None
                while True:
                    view = call("jobs/"+job["id"])
                    now = time.monotonic()-start
                    if first is None and any(c["state"]=="ready" for c in view["clips"]):
                        first = round(now, 3)
                    if view["state"] not in ("queued","running"):
                        break
                    if now > 900:
                        raise TimeoutError(f"Worker timed out on {name}.")
                    time.sleep(.25)
                outputs = []
                for clip in view["clips"]:
                    if clip["state"] != "ready":
                        raise RuntimeError(clip["error"])
                    artifact = clip["artifact"]
                    outputs.append({"start":clip["start_ms"],"end":clip["end_ms"],"captions":artifact["captions"],
                                    "width":artifact["width"],"height":artifact["height"],"upscaled":artifact["upscale"],
                                    "bytes":artifact["bytes"],"sha256":artifact["sha256"]})
                    ffmpeg("-i", state/job["id"]/(clip["id"]+".mp4"), "-f", "null", "-")
            finally:
                stop.set()
                monitor.join()
            result[name] = {"source_duration_ms":source["duration_ms"],"source_bytes":path.stat().st_size,
                            "state":view["state"],"selector":view["selector"],"first_s":first,"elapsed_s":round(now,3),"peak_rss_kib":maxima[0],
                            "observed_disk_growth_bytes":maxima[1],"outputs":outputs}
            print(name, view["state"], round(now, 3), len(outputs), flush=True)
            (root/"application.json").write_text(json.dumps(result,indent=2))
    finally:
        import signal
        server.send_signal(signal.SIGINT)
        server.wait(timeout=30)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("fixtures", "upstream", "app"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--openshorts", type=Path)
    parser.add_argument("--clipping", type=Path)
    parser.add_argument("--model", type=Path, default=Path("data/models/ggml-tiny.en.bin"))
    args = parser.parse_args()
    root = args.directory.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if args.stage == "fixtures":
        tutorial(root)
        for name in list(FIXTURES)[1:]:
            animated(root, name)
    elif args.stage == "upstream":
        audit(root, args.openshorts, args.clipping)
    else:
        measure(root, args.model.resolve())


if __name__ == "__main__":
    main()
