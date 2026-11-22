# Upstream import audit

| Upstream and pinned commit | File used | Active use |
| --- | --- | --- |
| [OpenShorts](https://github.com/mutonby/openshorts/tree/b3cd947bc48ad1fc8093bb8f8291b9fb02e9bb2f) | [ffmpeg_utils.py](https://github.com/mutonby/openshorts/blob/b3cd947bc48ad1fc8093bb8f8291b9fb02e9bb2f/ffmpeg_utils.py), `METADATA_SCRUB` | Strip inherited container and stream metadata from encoded delivery files |
| [opensource-clipping](https://github.com/NaufalRizqullah/opensource-clipping/tree/1f4e5bcf90f1edf7c1478ca52a683abdb07a5aa7) | [clipping/studio/ffmpeg_utils.py](https://github.com/NaufalRizqullah/opensource-clipping/blob/1f4e5bcf90f1edf7c1478ca52a683abdb07a5aa7/clipping/studio/ffmpeg_utils.py), `format_seconds` | Format source range labels |

Both imports live in `framefold/upstream.py` with their original MIT license texts in this directory. The OpenShorts notice records the separate commercial terms for `cloud/`. ClipFarm ships only the two audited utility snippets. Both routines are exercised on normal render and results requests in the integration tests. The benchmark records which upstream stages ran locally and their measured scope.

Reviewed the upstream selection, subtitle, studio and configuration paths to identify dependencies. Upstream semantic selection and diarization can require external providers or a Hugging Face token. ClipFarm packages its own selection, preview and renderer around local FFmpeg and optional whisper.cpp. FFmpeg, its linked codecs and libass, whisper.cpp, model weights, Pillow, LightGBM and fonts are independently distributed and have their own notices and terms.

## Asset provenance

- The generator uses a locally stored, AI-generated surf study, `web/surf-study.jpg`. The design atlas informed the layout and palette. The public demo uses stills taken from its credited example video.
- Barlow Condensed and DM Sans are self-hosted. Their SIL Open Font Licenses are included in `web/fonts/`.

- `web/example.jpg` is a crop of plate 01 from the user-supplied design atlas, retained for compatibility with earlier workspaces. Verify redistribution rights for the art before including it in a public build. The static export uses only files from the selected run ZIP and omits this frame.
- Integration fixtures are generated from FFmpeg test patterns and locally synthesized tutorial speech. The macOS tests use the installed system voice. The generated audio and the voice model are excluded from the source distribution.
- Optional Whisper tiny English weights are retrieved from the [whisper.cpp model mirror](https://huggingface.co/ggerganov/whisper.cpp). The local setup uses `ggml-tiny.en.bin`. Model files and run artifacts remain outside source control.
- The browser HTTPS smoke test uses the W3C-hosted [Sintel trailer](https://media.w3.org/2010/05/sintel/trailer.mp4). [Sintel project and licensing](https://durian.blender.org/). It is a test input and excluded from the source distribution.
