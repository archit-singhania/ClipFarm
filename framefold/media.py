"""FFmpeg analysis and rendering for source-relative millisecond ranges."""
import array
from functools import lru_cache
import hashlib
import json
import math
import re
import statistics
import subprocess
import tempfile
from pathlib import Path

from .config import setting
from .durations import bounds, windows

from .captions import overlay_inputs
from .ranking import rank
from .tracking import GRID_H, GRID_W, SAMPLE_FPS, tracking_filter
from .upstream import METADATA_SCRUB

FFMPEG = setting("FFMPEG", "ffmpeg")
FFPROBE = setting("FFPROBE", "ffprobe")
# FFmpeg's MOV demuxer disables external data references by default. Supplying
# its private option explicitly prevents other supported demuxers from opening.
INPUT = ["-protocol_whitelist", "file"]
PRESETS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080),
           "4:5": (1080, 1350), "3:4": (1080, 1440)}
VERSION = "windows-2/ffmpeg-2"
MUSIC_FORMATS = "mov,mp3,wav,aiff,flac,ogg,matroska,webm,aac"
# Aligned to the half-second signal grid. Model and decoder work is bounded
# per window even for a source lasting many hours.
WINDOW_MS = 300_000


def command(args, timeout=120, cwd=None):
    try:
        return subprocess.run(args, capture_output=True, check=True, timeout=timeout, cwd=cwd).stdout
    except FileNotFoundError as exc:
        raise ValueError(f"Install {args[0]} and restart the worker.") from exc
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"Media processing failed. {exc.stderr.decode(errors='replace')[-400:]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError("A media processing step timed out. Retry this run.") from exc


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


def probe_music(path):
    data = json.loads(command([FFPROBE, "-v", "error", *INPUT, "-format_whitelist", MUSIC_FORMATS,
                              "-show_streams", "-show_format", "-of", "json", str(path)], timeout=30))
    duration = float(data["format"].get("duration", 0))
    if not any(s["codec_type"] == "audio" for s in data["streams"]):
        raise ValueError("Choose a soundtrack containing an audio stream.")
    if not 0.2 <= duration <= 3600:
        raise ValueError("Choose a soundtrack between 0.2 seconds and one hour.")
    return round(duration * 1000)


def ffmpeg(source, *args, seek=None, input_duration=None, **kwargs):
    seek_args = ["-ss", str(seek)] if seek is not None else []
    if input_duration is not None:
        seek_args += ["-t", str(input_duration)]
    return command([FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                    "-threads", "2", "-filter_threads", "2", *INPUT, *seek_args, "-i", str(source), *args], **kwargs)


def analyze_window(source, metadata, directory, start, end, *, transcribe=True):
    """Decode one source window. Returned speech and signal times are local."""
    previous = min(500, start)
    span = end - start
    bounds = {"seek": start / 1000, "input_duration": span / 1000}
    frames = ffmpeg(source, "-map", f"0:{metadata['video_index']}", "-an", "-vf",
                    "fps=2:start_time=0,scale=32:24,format=gray", "-t", str((span+previous)/1000),
                    "-f", "rawvideo", "-",
                    seek=(start-previous)/1000, input_duration=(span+previous)/1000, timeout=900)
    size = 32 * 24
    fingerprints = []
    motion = [0.0]
    motion_cues = [0.0]
    for offset in range(size, len(frames), size):
        delta = [abs(a-b) for a, b in zip(frames[offset-size:offset], frames[offset:offset+size])]
        global_change = sum(delta) / size
        motion.append(global_change)
        cue = global_change
        if .35 <= global_change < 2.2:
            # A moving handle or fold may occupy only a few image blocks.
            # Require several changed blocks to reject isolated quantization flicker.
            blocks = [sum(sum(delta[row*32+x:row*32+x+4]) for row in range(y, y+4))/16
                      for y in range(0, 24, 4) for x in range(0, 32, 4)]
            local = statistics.mean(sorted(blocks, reverse=True)[:3])
            if local >= 8:
                cue = max(2.2, min(5.0, local / 4))
        motion_cues.append(cue)
    for offset in range(0, len(frames), size):
        frame = frames[offset:offset+size]
        # Eight by six spatial bins preserve composition while tolerating resize.
        fingerprints.append([sum(frame[y*32+x:y*32+x+4])//4
                             for y in range(2, 24, 4) for x in range(0, 32, 4)])
    bins = (span + 499) // 500
    def grid(values, padding):
        values = values[previous//500:previous//500 + bins]
        values.extend([padding] * (bins-len(values)))
        return values
    motion = grid(motion, 0.0)
    motion_cues = grid(motion_cues, 0.0)
    fingerprints = grid(fingerprints, fingerprints[-1] if fingerprints else [0]*48)
    energy, speech, notice = [], [], None
    if metadata["audio"]:
        samples = ffmpeg(source, "-map", "0:a:0", "-vn", "-t", str(span/1000), "-ac", "1", "-af",
                         "aresample=2000:async=1:first_pts=0", "-f", "s16le", "-", **bounds, timeout=900)
        pcm = array.array("h", samples)
        energy = [math.sqrt(statistics.mean(value * value for value in pcm[i:i+1000]))
                  for i in range(0, len(pcm), 1000)]
        energy = energy[:bins] + [0.0] * max(0, bins-len(energy))
        if transcribe and max(energy, default=0) > 160:
            cached = directory.parent / "models" / "ggml-tiny.en.bin"
            model = Path(setting("WHISPER_MODEL", cached)).expanduser().resolve()
            if model.is_file():
                wav = directory / "speech.wav"
                prefix = directory / "speech"
                try:
                    ffmpeg(source, "-map", "0:a:0", "-vn", "-t", str(span/1000), "-ac", "1", "-af",
                           "aresample=16000:async=1:first_pts=0", str(wav), **bounds, timeout=900)
                    command(["whisper-cli", "-m", str(model), "-f", str(wav), "-oj", "-of", str(prefix),
                             "-l", "auto", "-ng"], timeout=3600)
                    output = json.loads(prefix.with_suffix(".json").read_text())
                    speech = [{"start": max(0, s["offsets"]["from"]), "end": min(span, s["offsets"]["to"]),
                               "text": s["text"].strip()} for s in output["transcription"]
                              if max(0, s["offsets"]["from"]) < min(span, s["offsets"]["to"])
                              and s["text"].strip() and not re.fullmatch(r"\s*[\[(].*[\])]\s*", s["text"])]
                finally:
                    wav.unlink(missing_ok=True)
                    prefix.with_suffix(".json").unlink(missing_ok=True)
            else:
                notice = "Audio is present. Set up a local speech model and start a new run to generate captions."
    return {"motion": motion, "motion_cues": motion_cues, "energy": energy, "speech": speech, "notice": notice, "fingerprints": fingerprints}


def combine(windows):
    """Join adjacent windows, shifting cue times by their source-relative offset."""
    combined = {"motion": [], "motion_cues": [], "energy": [], "fingerprints": [], "speech": [], "notice": None}
    for offset, signals in windows:
        for key in ("motion", "motion_cues", "energy", "fingerprints"):
            combined[key].extend(signals[key])
        combined["speech"].extend({**cue, "start": cue["start"]+offset, "end": cue["end"]+offset}
                                  for cue in signals["speech"])
        combined["notice"] = combined["notice"] or signals["notice"]
    return combined


def analyze(source, metadata, directory, *, transcribe=True):
    """Provide a complete signal array for the offline editorial training tools."""
    duration = metadata["duration_ms"]
    return combine((start, analyze_window(source, metadata, directory, start,
                                          min(start+WINDOW_MS, duration), transcribe=transcribe))
                   for start in range(0, duration, WINDOW_MS))


def propose(signals, duration, preference=None):
    """Find bounded windows around spoken thoughts and sustained activity."""
    candidates = []
    # Measure activity in windows sized for this run before ranking. Auto
    # retains the original phrase and motion grouping.
    phrase_span = bounds(preference)[1] if preference else 35000
    event_samples = max(3, phrase_span // 500) if preference else 54
    current = []
    for segment in signals["speech"]:
        if current and (segment["start"] - current[-1]["end"] > 1600
                        or segment["end"] - current[0]["start"] > phrase_span):
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
        values = signals.get("motion_cues", signals[kind]) if kind == "motion" else signals[kind]
        active = [i for i, value in enumerate(values) if value >= threshold]
        groups = []
        for index in active:
            if not groups or index - groups[-1][-1] > 3:
                groups.append([])
            groups[-1].append(index)
        for group in groups:
            if len(group) < 3:
                continue
            for offset in range(0, len(group), event_samples):
                event = group[offset:offset+event_samples]
                if len(event) < 3:
                    continue
                start, end = event[0]*500 - 1200, (event[-1]+1)*500 + 1400
                strength = statistics.mean(values[i] for i in event) / threshold
                candidates.append((start, end, min(strength, 10) + min(len(event), 25) / 25, reason))
    rows = [(max(0, start), min(end, duration), score, reason)
            for start, end, score, reason in candidates if min(end, duration)-max(0, start) >= 2400]


    return windows(rows, signals, duration, preference)


def scene_signature(signals, start, end):
    fingerprints = signals.get("fingerprints", [])
    if not fingerprints or any(cue["start"] < end and cue["end"] > start for cue in signals["speech"]):
        return b""
    return bytes(pixel for fraction in (.25, .45, .65, .8)
                 for pixel in fingerprints[min(len(fingerprints)-1, (start + round((end-start)*fraction))//500)])


def same_scene(left, right):
    if not left or not right:
        return False
    total = peaks = 0
    for offset in range(0, len(left), 48):
        delta = [abs(x-y) for x,y in zip(left[offset:offset+48], right[offset:offset+48])]
        total += sum(delta)
        peaks += sum(sorted(delta, reverse=True)[:3])
        if total >= 5*len(left) or peaks >= 8*12:
            return False
    return True


def distinct_picks(ordered, count):
    picks = []
    accepted = []
    for start, end, reason, signature in ordered:
        if any(min(end, b)-max(start, a) > .25*min(end-start, b-a)
               or same_scene(signature, other) for a,b,other in accepted):
            continue
        accepted.append((start, end, signature))
        picks.append({"start_ms": start, "end_ms": end, "reason": reason})
        if len(picks) == count:
            break
    return picks


def select(signals, duration, count, selector=None):
    """Rank and deduplicate windows before assigning stable pick numbers."""
    ordered, version = rank(signals, propose(signals, duration, signals.get("duration")), selector)
    rows = ((a, b, reason, scene_signature(signals, a, b)) for (a, b, _, reason), _ in ordered)
    return distinct_picks(rows, count), version


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


def frame_filters(source, metadata, start, duration, width, height, framing, temporary):
    if framing == "track":
        frames = ffmpeg(source, "-t", str(duration), "-map", f"0:{metadata['video_index']}", "-an",
                        "-vf", f"fps={SAMPLE_FPS},scale={GRID_W}:{GRID_H},format=gray",
                        "-f", "rawvideo", "-", seek=start/1000, timeout=300)
        tracked = tracking_filter(frames, metadata["width"], metadata["height"], width, height, temporary)
        if tracked:
            return "track", tracked
        framing = "fit"
    direction = "increase" if framing == "crop" else "decrease"
    filters = f"scale={width}:{height}:force_original_aspect_ratio={direction}:force_divisible_by=2"
    filters += f",crop={width}:{height}" if framing == "crop" else f",pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
    return framing, filters


def mix_filters(track_index, duration, source_audio):
    common = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    bed = (f"[{track_index}:a:0]{common},atrim=duration={duration},asetpts=PTS-STARTPTS,"
           f"volume=0.24,afade=t=in:d=0.10,afade=t=out:st={max(0,duration-.25)}:d=0.25")
    if not source_audio:
        return [bed + "[mix]"]
    return [
        bed + "[bed]",
        f"[0:a:0]{common},apad,atrim=duration={duration},asplit[original][key]",
        "[bed][key]sidechaincompress=threshold=.025:ratio=10:attack=10:release=300[ducked]",
        "[original][ducked]amix=inputs=2:normalize=0:duration=longest,alimiter=limit=.96:level=0:latency=1[mix]",
    ]


@lru_cache(maxsize=1)
def has_subtitles_filter(binary):
    return bool(re.search(rb"\bsubtitles\s+V->V", command([binary, "-hide_banner", "-filters"])))


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
    music_id = options.get("music_id")
    try:
        with tempfile.TemporaryDirectory(dir=directory, prefix="render-") as runtime:
            temporary = Path(runtime)
            framing, filters = frame_filters(source, metadata, start, duration, width, height,
                                             options["framing"], temporary)
            extra_inputs = []
            graph = []
            video = f"[0:{metadata['video_index']}]{filters},setsar=1,fps=30"
            next_input = 1
            if cues:
                if has_subtitles_filter(FFMPEG):
                    srt = destination.with_suffix(".srt").name
                    video += f",subtitles={srt}:force_style='Alignment=2,MarginV=36,FontSize=18,Outline=2'"
                else:
                    extra_inputs.extend(overlay_inputs(cues, temporary, width, height, duration))
                    graph.append(video + "[base]")
                    video = f"[base][1:v]overlay=x=0:y=H-h-{round(height*.075)}:eof_action=pass"
                    next_input += 1
            graph.append(video + "[out]")
            audio_map = "0:a:0?"
            if music_id:
                # The identifier is validated and owner-scoped by the control API.
                if not re.fullmatch(r"[a-f0-9]{32}", music_id):
                    raise ValueError("Invalid soundtrack.")
                track = directory.parent / (music_id + ".music")
                extra_inputs.extend(["-stream_loop", "-1", *INPUT, "-format_whitelist", MUSIC_FORMATS,
                                     "-i", str(track)])
                graph.extend(mix_filters(next_input, duration, metadata["audio"]))
                audio_map = "[mix]"
            ffmpeg(source, *extra_inputs, "-t", str(duration), "-filter_complex_threads", "2",
                   "-filter_complex", ";".join(graph), "-map", "[out]", "-map", audio_map,
                   "-c:v", "libx264", "-threads", "4", "-preset", "veryfast",
                   "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", *METADATA_SCRUB,
                   "-movflags", "+faststart", str(staging), seek=start/1000, cwd=directory, timeout=1800)
        actual = probe(staging)
        if (actual["width"], actual["height"], actual["codec"]) != (width, height, "h264"):
            raise ValueError("The rendered file does not match the export preset.")
        tolerance = 50 if options.get("duration", {}).get("mode") == "exact" else 250
        if abs(actual["duration_ms"] - (end-start)) > tolerance or actual["audio"] != (metadata["audio"] or bool(music_id)):
            raise ValueError("The rendered timing or audio failed verification.")
        ffmpeg(staging, "-map", "0:v:0", "-frames:v", "1", "-f", "null", "-", timeout=30)
        with staging.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        actual.update(bytes=staging.stat().st_size, sha256=digest, captions=len(cues),
                      caption_bounds_ms=[cues[0][0], cues[-1][1]] if cues else None,
                      framing_applied=framing, soundtrack=bool(music_id),
                      upscale=(min if framing == "fit" else max)(width/metadata["width"], height/metadata["height"]) > 1,
                      verified=True, version=VERSION)
        thumbnail = destination.with_suffix(".jpg")
        try:
            ffmpeg(staging, "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4",
                   str(thumbnail), seek=duration*.45, timeout=30)
            actual["poster"] = thumbnail.is_file()
        except ValueError:
            thumbnail.unlink(missing_ok=True)
            actual["poster"] = False
        staging.replace(destination)
        return actual
    finally:
        staging.unlink(missing_ok=True)
