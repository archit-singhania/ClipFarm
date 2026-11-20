# Framefold

A local workspace for automatically turning videos into short, watermark-free clips. Upload a file or paste a permitted direct HTTPS video URL, choose **Generate clips**, and download individual MP4s or a ZIP from Results. Details lets you save a separate copy with a new trim, crop or caption setting.

## Start

Use Python 3.11+, FFmpeg and FFprobe on macOS or Linux. FFmpeg needs its `libx264` encoder. Install the Python dependency for caption rendering when FFmpeg lacks libass.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m framefold.server
```

Open [Framefold locally](http://127.0.0.1:8765). The server starts a separate worker and writes private data under `data/`. To run the worker separately, use `python -m framefold.server --no-worker` and `python -m framefold.worker`. Both processes must use the same working directory or `FRAMEFOLD_DATA`. Use the same browser profile and localhost address to reopen previous runs.

### Enable keyless speech captions

Install [whisper.cpp](https://github.com/ggml-org/whisper.cpp) so `whisper-cli` is on PATH. Put the English tiny weights at `data/models/ggml-tiny.en.bin`, or set `FRAMEFOLD_WHISPER_MODEL` to an absolute path for a compatible local model. The service uses these files offline. A multilingual model is needed for multilingual transcription. When a model is missing, the app can still select visual/audio candidates and displays a caption setup message. Sources without audio skip speech recognition.

```sh
mkdir -p data/models
curl -fL https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.en.bin \
  -o data/models/ggml-tiny.en.bin
```

The renderer uses FFmpeg's libass subtitles filter where available, with a Pillow overlay fallback. `FRAMEFOLD_CAPTION_FONT` can specify a local font with the required glyph coverage. Captions are burned where detected and included as SRT and VTT. There is no paid API dependency. Decoding and encoding use the host's CPU, disk and electricity.

## Runs and recovery

- The link importer accepts direct public HTTPS media files. It checks each redirect and only connects to a validated public address. Use upload for videos behind a login or embedded in a webpage. The file limit is 1 GB and the duration limit is one hour, with an UHD source pixel cap.
- The worker records durable checkpoints for source preparation, analysis and picks. After restart it resumes at the last checkpoint and skips ready files. Cancel prevents additional work at the next stage or clip boundary. In-progress encoding can finish before cancellation takes effect.
- Results exposes files as soon as they pass verification. A partial run keeps ready files and offers retries for failed clips. A static or low-signal source may return fewer clips or none.
- Export supports 9:16, 16:9, 1:1, 4:5 and 3:4 in HD or double-dimension UHD presets. The default vertical HD is 1080 × 1920. Fit preserves the complete image. Center crop fills the frame. Results labels upscaled outputs.
- The service binds to loopback. Browser sessions scope sources, runs and downloads. Local files remain on disk for reopening. Automated retention is pending. Stop the app before moving its data directory. The loopback service is designed for one local workstation.

## Static example

Download a ZIP from a run you have permission to publish, then build a static folder. It includes verified clips, caption files when present, the theme picker and a labeled Example run page.

```sh
python3 -m framefold.demo ~/Downloads/framefold-clips.zip /tmp/framefold-example --name "My example source"
```

Serve the generated folder over HTTP. It can be deployed on a static host without a rendering worker. Hosting and publication are separate steps.

## Check the implementation

```sh
python3 -m compileall -q framefold ml
node --check web/app.js
node --check web/theme.js
python3 -m unittest discover -s tests -v
```

The speech test additionally uses `FRAMEFOLD_WHISPER_MODEL` and macOS `say`, or a permitted direct video link set in `FRAMEFOLD_TEST_SPEECH_URL`. See the [implementation summary](IMPLEMENTATION.md), [upstream audit](third_party/README.md) and [local ranker workflow](ml/README.md).
