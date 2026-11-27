# ClipFarm

Upload a video or paste a public recording link, choose **3, 5, 10, 15, 20, 50, or 100 clips**, and select **Generate clips**. Download individual MP4s or a ZIP from Results. Details lets you save a separate copy with a new trim, crop or caption setting.

[Play the public ClipFarm demo](https://clipfarm-screening-room.atsin2526.chatgpt.site). The demo has three downloadable HD excerpts from *Sintel*. Use the local app below for your own footage.

## Start

Use Python 3.11+, FFmpeg and FFprobe on macOS or Linux. FFmpeg needs its `libx264` encoder. The Python requirements include yt-dlp for recording links and Pillow for caption rendering.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m clipfarm
```

Open [ClipFarm locally](http://127.0.0.1:8765). The server starts a separate worker and writes private data under `data/`. To run the worker separately, use `python -m framefold.server --no-worker` and `python -m framefold.worker`. Both processes must use the same working directory or `CLIPFARM_DATA`. Use the same browser profile and localhost address to reopen previous runs.

Select **Theme** in the header to change the workspace palette. The five dark options are Charcoal, Oxblood, Petrol, Ultraviolet and Ember. The five light options are Forest, Navy, Porcelain, Matcha and Rosé. Your browser remembers the selection and applies it across open ClipFarm tabs on the same site.

### Enable keyless speech captions

Install [whisper.cpp](https://github.com/ggml-org/whisper.cpp) so `whisper-cli` is on PATH. Put the English tiny weights at `data/models/ggml-tiny.en.bin`, or set `CLIPFARM_WHISPER_MODEL` to an absolute path for a compatible local model. The service uses these files offline. A multilingual model is needed for multilingual transcription. When a model is missing, the app can still select visual/audio candidates and displays a caption setup message. Sources without audio skip speech recognition.

```sh
mkdir -p data/models
curl -fL https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.en.bin \
  -o data/models/ggml-tiny.en.bin
```

Under **Export settings → Highlight selection**, General highlights is the default. The experimental film-trailer model uses a small local LightGBM ranker trained on matches between openly licensed films and their official trailers. The server records the selected model for each run. For the training evidence and limitations see the [model workflow](ml/README.md).

An original pick has an optional **Rate this pick** form in Details. Record a 0–3 judgment, genre and training permission, then export that run's JSONL locally. For blinded review of all proposed windows, use the review tool under `ml/`. When combining exports, group alternate edits of the same source together before evaluating the model.

The renderer uses FFmpeg's libass subtitles filter where available, with a Pillow overlay fallback. `CLIPFARM_CAPTION_FONT` can specify a local font with the required glyph coverage. Captions are burned where detected and included as SRT and VTT. There is no paid API dependency. Decoding and encoding use the host's CPU, disk and electricity.

## Runs and recovery

- Paste a link to a single finished recording on YouTube, Twitch, Kick, Rumble or another supported site. Common bare links such as `youtu.be/…` also work. The importer handles public direct media, supported embedded players, and finished HLS/DASH recordings. The provider must make playable, unprotected media available from your network. For private, expired, or restricted links, upload an original you have permission to use.
- Video uploads and recording imports have no fixed file-size or duration ceiling. Allow disk space for the original, temporary tracks and exported clips. The app retains 512 MiB of free space. Videos must contain at least two seconds of playable footage within the UHD source pixel limit. Keep the tab open during transfer. Uploads display progress.
- On long recordings the worker analyzes and transcribes five-minute windows. It writes each completed window to disk and ranks candidates across the full source. After restart it resumes at the last checkpoint and skips ready files. Cancel stops at the next analysis window, stage or clip boundary.
- Results and History display ready original clips against the requested target. Edits are counted separately. Results exposes files as soon as they pass verification, and a full ZIP download streams them without making another on-disk copy. A static or low-signal source may return fewer clips or none. A partial run keeps ready files and offers retries for failed clips.
- Export supports 9:16, 16:9, 1:1, 4:5 and 3:4 in HD or double-dimension UHD presets. The default vertical HD is 1080 × 1920. Fit preserves the complete image. Center crop fills the frame. Results labels upscaled outputs.
- **Follow subject** estimates the movement of a compact foreground subject and smooths the crop. It falls back to the full frame for ambiguous movement, cuts, or extended tracking loss. **Center crop** gives a fixed alternative.
- A soundtrack can be attached in Export settings or on a revision. The renderer loops it to the clip length and lowers its level under foreground audio using FFmpeg sidechain compression.
- The service binds to loopback. Browser sessions scope sources, runs and downloads. Local files remain on disk for reopening. Set `CLIPFARM_RETENTION_DAYS` to a positive whole number to expire terminal runs older than that many days, measured from run creation. The idle worker checks hourly and retains source files still in use by another run. Stop the app before moving its data directory. The loopback service is designed for one local workstation.

## Static example

Download a ZIP from a run you have permission to publish, then build a static folder. It includes verified clips, caption files when present, the theme picker and a labeled Example run page.

```sh
python3 -m framefold.demo ~/Downloads/clipfarm-clips.zip /tmp/clipfarm-example --name "My example source"
```

Serve the generated folder over HTTP. Supply `--credit` when publishing material that requires attribution. The static demo includes pre-rendered MP4s, caption files, and a downloadable bundle. The local workspace handles new uploads.

## Check the implementation

```sh
python3 -m compileall -q framefold ml
node --check web/app.js
node --check web/theme.js
python3 -m unittest discover -s tests -v
```

The optional browser matrix uses `npm ci --prefix tests/browser` and `npm test --prefix tests/browser` with Chrome installed.

Set `CLIPFARM_TEST_LARGE_BATCH=1` to include the two-hour, 100-export HTTP test. It transfers more than 1 GiB and verifies the SHA-256 of each MP4 in the ZIP.

The speech test additionally uses `FRAMEFOLD_WHISPER_MODEL` and macOS `say`, or a permitted direct video link set in `FRAMEFOLD_TEST_SPEECH_URL`. See the [implementation summary](IMPLEMENTATION.md), [upstream audit](third_party/README.md) and [local ranker workflow](ml/README.md).

### Test it manually

1. Start ClipFarm and open the generator in the same browser you use for your runs. [Local generator](http://127.0.0.1:8765/)
2. Select **Video link**, paste `https://youtu.be/HOfdboHvshg`, choose **5** under **Clip target**, and click **Generate clips**. The link points to Blender’s 52-second *Sintel* trailer. [Original video](https://www.youtube.com/watch?v=HOfdboHvshg)
3. When the run finishes, play a pick. Choose **Download all**, unzip the archive, and compare the number of MP4s with the ready count in Results. Reopen **My runs** in the same browser to verify that the run is saved.
4. For a longer test, select **New run**, then **Upload file**. Choose a recording with many distinct moments, set the target to **100**, and keep the tab open during transfer. Results shows how many original picks finished. A short or repetitive recording can yield fewer than the target.

### Clip duration

Choose **Auto** for natural highlights, or choose a preset or custom length between one second and five minutes. **Target** permits a 20% deviation to reach a timed sentence ending or fit the source. **Exact** selects the requested interval and verifies the rendered file within 50 milliseconds, allowing for the 30 fps output and container timing. **Range** keeps source intervals between the specified minimum and maximum. Target and Range share the existing 250 ms container verification tolerance. All bounds are stored as integer milliseconds.

The generator rejects sources shorter than the required minimum. ClipFarm ranks evidence-backed windows before removing overlapping or visually repeated picks. For configured lengths of 30 seconds or more, at least 15% of each selected interval must contain detected speech, motion or audio activity. A run can finish with fewer clips than requested. Results show measured export durations. In Details, trim and save a separate copy between one second and five minutes.
