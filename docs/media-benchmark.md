# ClipFarm media benchmark

The test uses six original, locally generated fixtures for the technical tasks in T01, T05, T11 and T14. The machine was an eight-core Apple M3 with 16 GiB of unified memory, macOS 26.5.2 and FFmpeg 8.1.2. The data volume had 4.91 GiB available before the runs. CPU libx264 encoded the outputs. Speech recognition used the cached whisper.cpp English tiny model with GPU acceleration disabled.

## Fixture inventory and scope

| Fixture | Original content | Expected event or control interval in seconds |
| --- | --- | --- |
| Tutorial | Twelve 50-second educational chapters at 1920 × 1080, with an original spoken instruction and a quiet practice interval in each chapter | A complete spoken step at 50–65, with subsequent steps at 50-second intervals |
| Silent craft | Two paper folds with a visible final crease and folded shape | Fold and hold at 2–6 and 10–14 |
| Sport | A ball crosses a drawn court and returns in a second action. Shapes are drawn at native 3840 × 2160 | First crossing at 2–6 and return at 10–14 |
| Screencast | A mock timeline marks an in point, then adjusts an out point | First adjustment at 2–6 and second at 10–14 |
| Music | Original synthesized 330 Hz passages with animated meters | Tone and movement at 2–6 and 10–14 |
| Mixed | Timeline animation and the original spoken review instruction | Marking movement at 2–6, with narration over the edit |

The generator contains the geometry, scripts, signals and timestamps. The installed macOS system voice synthesized the local test narration. Generated voice assets and models are excluded from distribution. These sources exercise timing, encoding, motion and audio behavior. They do not constitute a held-out human-rated training corpus.

## Measurement protocol

A local client uploaded each file through the production API, created a job, observed per-clip readiness and decoded every verified MP4 to completion using FFmpeg. Elapsed times include upload, probe, queue wait, inference and rendering. Polling used a 250 ms interval. Memory figures are the largest observed aggregate RSS of the server, worker and child processes, sampled with `ps`. Shared pages may appear in more than one process. Disk figures record the largest observed increase in bytes under the private data directory for that job, including its original and outputs. The monitor can miss peaks between samples. Encoding is CPU-based. Model download, fixture creation and post-run decode checks are excluded from the elapsed times.

Each upstream pipeline received a fixed, prescribed cut. That measures its eligible CPU rendering stages, while the ClipFarm job includes automatic selection. These timings have different scopes. The OpenShorts stage runs `ffmpeg_utils.cut_clip` with its own encoder, frame-rate and audio configuration. The opensource-clipping stage runs `clipping/studio/ffmpeg_utils.py` encoder discovery, MP4 argument construction and the upstream progress runner. The latter needed a small FFmpeg input/range wrapper; neither workflow calls external selection or publishing services. Their separately configured provider, GPU, model-download and diarization paths were excluded from these local stage measurements. Exact source locations, revisions and licenses are documented in the [upstream audit](../third_party/README.md).

## Reproduce

Run these commands from the repository root in the documented application environment. The fixtures stage requires macOS speech synthesis, Pillow, FFmpeg and FFprobe. The app stage requires a loopback listener and process-RSS queries. Choose a fresh private benchmark directory per measurement.

```sh
python scripts/benchmark_media.py fixtures /tmp/clipfarm-benchmark
python scripts/benchmark_media.py upstream /tmp/clipfarm-benchmark \
  --openshorts /path/to/pinned/openshorts --clipping /path/to/pinned/opensource-clipping
python scripts/benchmark_media.py app /tmp/clipfarm-benchmark \
  --model data/models/ggml-tiny.en.bin
```

The benchmark writes source hashes, ranges, encoder outcomes and resource observations to `upstream.json` and `application.json` in that directory. Rebuilding the generated voice depends on the installed system voice and operating system.

## Measured results

| Fixture | Clips | First file | Completed | Peak aggregate RSS | Peak disk growth | OpenShorts cut | opensource-clipping cut |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Tutorial | 3 | 61.39 s | 65.88 s | 510.0 MiB | 22.59 MiB | 0.849 s | 1.184 s |
| Craft | 2 | 1.38 s | 2.44 s | 295.8 MiB | 0.24 MiB | 0.138 s | 0.115 s |
| Sport | 2 | 5.40 s | 14.03 s | 983.9 MiB | 0.77 MiB | 1.487 s | 2.291 s |
| Screencast | 2 | 1.37 s | 2.42 s | 303.8 MiB | 0.11 MiB | 0.109 s | 0.085 s |
| Music | 2 | 2.96 s | 4.31 s | 314.3 MiB | 0.64 MiB | 0.278 s | 0.121 s |
| Mixed | 2 | 3.55 s | 4.35 s | 334.4 MiB | 0.60 MiB | 0.251 s | 0.094 s |

The ten-minute tutorial returned three HD cuts with two caption cues each. One selected range starts during a practice interval and extends into the next instruction. The two native UHD sport clips retained source resolution. The initial screencast run missed both small timeline gestures. A second run with the localized motion cue found two picks. These figures come from the second run.
