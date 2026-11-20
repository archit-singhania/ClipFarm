"""FFmpeg analysis and rendering for source-relative millisecond ranges."""
import array
import hashlib
import json
import math
import os
import re
import statistics
import subprocess
import tempfile
from pathlib import Path

from .captions import overlay_inputs
from .ranking import rank
from .upstream import METADATA_SCRUB

FFMPEG = os.environ.get("FRAMEFOLD_FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("FRAMEFOLD_FFPROBE", "ffprobe")
INPUT = ["-protocol_whitelist", "file", "-enable_drefs", "0"]
PRESETS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080),
           "4:5": (1080, 1350), "3:4": (1080, 1440)}
VERSION = "rules-1/ffmpeg-1"


def command(args, timeout=120, cwd=None):
    try:
        return subprocess.run(args, capture_output=True, check=True, timeout=timeout, cwd=cwd).stdout
    except FileNotFoundError as exc:
        raise ValueError(f"Install {args[0]} and restart the worker.") from exc
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"Media processing failed. {exc.stderr.decode(errors='replace')[-400:]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Media processing timed out. Try a shorter source or retry.") from exc


def probe(path, restricted=True):
    # Disallow manifest and playlist demuxers before opening untrusted media.
    limits = ["-format_whitelist", "mov,matroska,webm,avi,mpeg,mpegts,flv,ogg"] if restricted else []
    data = json.loads(command([
        FFPROBE, "-v", "error", *INPUT, *limits, "-show_streams", "-show_format", "-of", "json", str(path)
    ], timeout=30))
    video = next((s for s in data["streams"] if s["codec_type"] == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    if not video:
        raise ValueError("The file has no playable video track.")
    duration = float(data["format"].get("duration", 0))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("The video must have a known duration.")
    rotation = next((s["rotation"] for s in video.get("side_data_list", []) if "rotation" in s), 0)
    width, height = video["width"], video["height"]
    if abs(rotation) % 180 == 90:
        width, height = height, width
    if not 0 < width * height <= 4096 * 2304:
        raise ValueError("Source dimensions exceed the supported UHD limit.")
    return {"duration_ms": round(duration * 1000), "width": width, "height": height,
            "codec": video["codec_name"], "format": data["format"]["format_name"],
            "video_index": video["index"], "rotation": rotation, "time_base": video["time_base"],
            "tracks": [{"index": s["index"], "type": s["codec_type"], "codec": s["codec_name"]}
                       for s in data["streams"]],
            "audio": any(s["codec_type"] == "audio" for s in data["streams"])}


def ffmpeg(source, *args, **kwargs):
    return command([FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                    "-threads", "2", "-filter_threads", "2", *INPUT, "-i", str(source), *args], **kwargs)


def analyze(source, metadata, directory):
    frames = ffmpeg(source, "-map", f"0:{metadata['video_index']}", "-an", "-vf",
                    "fps=2,scale=32:24,format=gray", "-f", "rawvideo", "-", timeout=600)
    size = 32 * 24
    fingerprints = []
    motion = [0.0]
    for offset in range(size, len(frames), size):
        motion.append(sum(abs(a - b) for a, b in zip(
            frames[offset-size:offset], frames[offset:offset+size]
        )) / size)
    for offset in range(0, len(frames), size):
        frame = frames[offset:offset+size]
        # Eight by six spatial bins preserve composition while tolerating resize.
        fingerprints.append([sum(frame[y*32+x:y*32+x+4])//4
                             for y in range(2, 24, 4) for x in range(0, 32, 4)])
    energy, speech, notice = [], [], None
    if metadata["audio"]:
        samples = ffmpeg(source, "-vn", "-ac", "1", "-ar", "2000", "-f", "s16le", "-", timeout=600)
        pcm = array.array("h", samples)
        energy = [math.sqrt(statistics.mean(value * value for value in pcm[i:i+1000]))
                  for i in range(0, len(pcm), 1000)]
        if max(energy, default=0) > 160:
            cached = directory.parent / "models" / "ggml-tiny.en.bin"
            model = Path(os.environ.get("FRAMEFOLD_WHISPER_MODEL", cached)).expanduser().resolve()
            if model.is_file():
                wav = directory / "speech.wav"
                ffmpeg(source, "-vn", "-ar", "16000", "-ac", "1", str(wav), timeout=600)
                prefix = directory / "speech"
                try:
                    command(["whisper-cli", "-m", str(model), "-f", str(wav), "-oj", "-of", str(prefix),
                             "-l", "auto", "-ng"], timeout=3600)
                    output = json.loads(prefix.with_suffix(".json").read_text())
                    speech = [{"start": s["offsets"]["from"], "end": s["offsets"]["to"],
                               "text": s["text"].strip()} for s in output["transcription"]
                              if s["text"].strip() and not re.fullmatch(r"\s*[\[(].*[\])]\s*", s["text"])]
                finally:
                    wav.unlink(missing_ok=True)
                    prefix.with_suffix(".json").unlink(missing_ok=True)
            else:
                notice = "Audio is present. Set up a local speech model and start a new run to generate captions."
    return {"motion": motion, "energy": energy, "speech": speech, "notice": notice, "fingerprints": fingerprints}


def propose(signals, duration):
    """Find bounded windows around spoken thoughts and sustained activity."""
    candidates = []
    current = []
    for segment in signals["speech"]:
        if current and (segment["start"] - current[-1]["end"] > 1600
                        or segment["end"] - current[0]["start"] > 35000):
            candidates.append(speech_candidate(current))
            current = []
        current.append(segment)
        if re.search(r"[.!?。！？]$", segment["text"]) and segment["end"] - current[0]["start"] >= 4500:
            candidates.append(speech_candidate(current))
            current = []
    if current:
        candidates.append(speech_candidate(current))
    for kind, threshold, reason in [
        ("motion", 2.2, "Sustained visual motion, with context before and after."),
        ("energy", 220, "A distinct rise in audio activity, with surrounding context."),
    ]:
        if kind == "energy" and signals["speech"]:
            continue
        values = signals[kind]
        active = [i for i, value in enumerate(values) if value >= threshold]
        groups = []
        for index in active:
            if not groups or index - groups[-1][-1] > 3:
                groups.append([])
            groups[-1].append(index)
        for group in groups:
            if len(group) < 3:
                continue
            for offset in range(0, len(group), 54):
                event = group[offset:offset+54]
                if len(event) < 3:
                    continue
                start, end = event[0]*500 - 1200, (event[-1]+1)*500 + 1400
                strength = statistics.mean(values[i] for i in event) / threshold
                candidates.append((start, end, min(strength, 10) + min(len(event), 25) / 25, reason))
    return [(max(0, start), min(end, duration), score, reason)
            for start, end, score, reason in candidates if min(end, duration)-max(0, start) >= 2400]


def repeated_scene(signals, left, right):
    fingerprints = signals.get("fingerprints", [])
    if not fingerprints or signals["speech"]:
        return False

    def signature(start, end):
        return [fingerprints[min(len(fingerprints)-1, (start + round((end-start)*fraction))//500)]
                for fraction in (.25, .45, .65, .8)]

    a, b = signature(*left), signature(*right)
    return statistics.mean(abs(x-y) for first, second in zip(a, b) for x, y in zip(first, second)) < 5


def select(signals, duration, count):
    """Rank and deduplicate windows before assigning stable pick numbers."""
    ordered, version = rank(signals, propose(signals, duration))
    picks = []
    for (start, end, _, reason), score in ordered:
        if any(
            min(end, item["end_ms"]) - max(start, item["start_ms"])
            > 0.25 * min(end-start, item["end_ms"]-item["start_ms"])
            or repeated_scene(signals, (start, end), (item["start_ms"], item["end_ms"])) for item in picks
        ):
            continue
        picks.append({"start_ms": start, "end_ms": end, "reason": reason})
        if len(picks) == count:
            break
    return picks, version


def speech_candidate(segments):
    first, last = segments[0], segments[-1]
    complete = bool(re.search(r"[.!?。！？]$", last["text"]))
    return (first["start"] - 250, last["end"] + 350, 12 if complete else 7,
            "Speech ends at a sentence boundary." if complete else "Speech grouped at a pause.")


def timestamp(ms, separator):
    seconds, fraction = divmod(ms, 1000)
    minutes, second = divmod(seconds, 60)
    hour, minute = divmod(minutes, 60)
    return f"{hour:02}:{minute:02}:{second:02}{separator}{fraction:03}"


def render(source, metadata, clip, directory, speech):
    options = json.loads(clip["options"])
    width, height = PRESETS[options["ratio"]]
    if options["quality"] == "UHD":
        width, height = width * 2, height * 2
    start, end = clip["start_ms"], clip["end_ms"]
    duration = (end-start) / 1000
    destination = directory / (clip["id"] + ".mp4")
    staging = destination.with_suffix(".tmp.mp4")
    cues = [(max(start, s["start"])-start, min(end, s["end"])-start, s["text"])
            for s in speech if s["start"] < end and s["end"] > start] if options["captions"] else []
    for suffix, separator in (("srt", ","), ("vtt", ".")):
        if cues:
            body = "\n".join(f"{i}\n{timestamp(a, separator)} --> {timestamp(b, separator)}\n{t}\n"
                             for i, (a, b, t) in enumerate(cues, 1))
            destination.with_suffix("." + suffix).write_text(("WEBVTT\n\n" if suffix == "vtt" else "") + body)
        else:
            destination.with_suffix("." + suffix).unlink(missing_ok=True)
    crop = options["framing"] == "crop"
    direction = "increase" if crop else "decrease"
    filters = f"scale={width}:{height}:force_original_aspect_ratio={direction}:force_divisible_by=2"
    filters += f",crop={width}:{height}" if crop else f",pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
    filters += ",setsar=1,fps=30"
    try:
        with tempfile.TemporaryDirectory(dir=directory, prefix="captions-") as temporary:
            extra_inputs = []
            mapping = ["-map", f"0:{metadata['video_index']}", "-vf", filters]
            if cues:
                available = command([FFMPEG, "-hide_banner", "-filters"])
                if re.search(rb"\bsubtitles\s+V->V", available):
                    # Shift the cue timestamps to the source clock because output seek follows filters.
                    absolute = Path(temporary) / "source.srt"
                    absolute.write_text("\n".join(
                        f"{i}\n{timestamp(a+start, ',')} --> {timestamp(b+start, ',')}\n{t}\n"
                        for i, (a, b, t) in enumerate(cues, 1)
                    ))
                    relative = absolute.relative_to(directory)
                    mapping[-1] += f",subtitles={relative}:force_style='Alignment=2,MarginV=36,FontSize=18,Outline=2'"
                else:
                    extra_inputs = overlay_inputs([(a+start, b+start, text) for a, b, text in cues],
                                                  Path(temporary), width, height, end/1000)
                    graph = (f"[0:{metadata['video_index']}]{filters}[base];"
                             f"[base][1:v]overlay=x=0:y=H-h-{round(height*.075)}:eof_action=pass[out]")
                    mapping = ["-filter_complex_threads", "2", "-filter_complex", graph, "-map", "[out]"]
            ffmpeg(source, *extra_inputs, "-ss", str(start/1000), "-t", str(duration), *mapping,
                   "-map", "0:a:0?", "-c:v", "libx264", "-threads", "4", "-preset", "veryfast",
                   "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", *METADATA_SCRUB,
                   "-movflags", "+faststart", str(staging), cwd=directory, timeout=1800)
        actual = probe(staging)
        if (actual["width"], actual["height"], actual["codec"]) != (width, height, "h264"):
            raise ValueError("The rendered file does not match the export preset.")
        if abs(actual["duration_ms"] - (end-start)) > 250 or actual["audio"] != metadata["audio"]:
            raise ValueError("The rendered timing or audio failed verification.")
        ffmpeg(staging, "-map", "0:v:0", "-frames:v", "1", "-f", "null", "-", timeout=30)
        with staging.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        actual.update(bytes=staging.stat().st_size, sha256=digest, captions=len(cues),
                      caption_bounds_ms=[cues[0][0], cues[-1][1]] if cues else None,
                      upscale=(max if crop else min)(width/metadata["width"], height/metadata["height"]) > 1,
                      verified=True, version=VERSION)
        thumbnail = destination.with_suffix(".jpg")
        try:
            ffmpeg(staging, "-ss", str(duration * .45), "-frames:v", "1",
                   "-vf", "scale=480:-2", "-q:v", "4", str(thumbnail), timeout=30)
            actual["poster"] = thumbnail.is_file()
        except ValueError:
            thumbnail.unlink(missing_ok=True)
            actual["poster"] = False
        staging.replace(destination)
        return actual
    finally:
        staging.unlink(missing_ok=True)
