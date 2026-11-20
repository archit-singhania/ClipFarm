# Framefold implementation summary

The local application was built from the empty ClipFarm checkout. The latest work chat supplied the automatic-first product intent. The implementation plan described the sequence and verification gates. The roadmap and atlas supplied release scope, states and visual references. Sample artefacts and estimates in those documents were treated as reference material.

| Tickets | Implementation and status |
| --- | --- |
| T01 | Inspected both public repositories, pinned commits and imported two narrow MIT utilities with notices. The upstream six-fixture benchmark still needs the six source videos and expected annotations. |
| T02–T03 | Generator, direct HTTPS/upload inputs, export options, preview, My runs, durable scoped sources, idempotent jobs and progress. The single local worker uses a file lock and SQLite stage checkpoints. |
| T04–T05 | Rules combine sustained motion, audio activity and speech boundaries, then filter temporal overlap and visually similar silent events. FFmpeg delivers verified individual MP4s, subtitle sidecars and ZIPs. One-action productive runs and the static negative case passed. The named ten-minute annotated tutorial remains to be tested. |
| T06–T08 | Versioned candidate-feature export, labeling rubric, grouped LightGBM training, holdout calculations and checksum-validated local serving with fallback. Synthetic training and inference passed a smoke test. The rights-cleared corpus, human labels, coverage study and production model evaluation remain outstanding. |
| T09–T10 | Ranked result cards, run detail, direct downloads and immutable optional copies. Three persisted themes, keyboard picker and responsive layouts. The browser checks cover keyboard theme selection and reload, plus mobile and desktop layout. Full zoom, assistive-technology and all-route theme matrix remain unverified. |
| T11 | Burned captions, SRT/VTT, fit and center crop, five aspect ratios, HD/UHD and upscale labeling. A 2160 × 2160 revision passed. Subject tracking, soundtrack ducking and the complete UHD/media matrix remain open. |
| T12 | Streaming imports, request/size/duration checks, pinned HTTPS with redirect validation, scoped downloads, checkpoint recovery, cancellation and retries. The full disk-failure and concurrent fault matrix and retention policy automation remain open. |
| T13–T14 | Local startup, migrations, health status, cached offline speech path, reproducible fixture tests and static export tooling. Public publication, clean-machine verification, broader benchmarks, and production release evidence remain open. |

## Verification

Python syntax and JavaScript syntax checks passed. The integration suite drove the production API and worker on Apple Silicon with FFmpeg 8.1.2 and a local Whisper tiny English model. The media worker also passed with network access denied. Each MP4 in the integration checks was fully decoded after download. Source ranges are source-relative, start-inclusive/end-exclusive milliseconds.

| Input and path | Verified output |
| --- | --- |
| 22-second generated silent video, upload | Two H.264 1080 × 1920 MP4s, each 8.100 s, with different checksums, no audio stream and no captions. Source ranges 1.800–9.900 s and 12.800–20.900 s. |
| Generated two-part spoken tutorial, upload | Two H.264 1080 × 1920 MP4s, 6.267 s and 6.300 s. Caption bounds 0.000–5.900 s and 0.250–5.930 s relative to the clips. |
| W3C-hosted Sintel trailer, direct HTTPS in browser | One Generate action exposed three verified H.264 1080 × 1920 MP4s with source ranges, individual downloads and a ZIP control. Durations were 6.367 s, 4.600 s and 3.600 s. Each clip contains a timed caption. This was a short public movie fixture. |
| Static color video, upload | Zero matches with an explanatory state. |
| Local-network HTTPS address | Rejected before import. |
| Interrupted silent run and repeated Generate request | Worker restarted during the render checkpoint. Verified outputs survived, and each repeated key returned the original job. |
| Revision | Original artefact manifest preserved. A separate UHD square copy rendered successfully. |

HTTP range playback, private download scope, attachment headers, SRT timing and ZIP contents were checked by the integration suite. Each intermediate artefact is private. A production model still requires human-reviewed training data. Test-only LightGBM features exercised the training and inference commands, with no performance claim.

The paused-render and media gates were verified using these small fixtures. The hardware cost of a longer HD or UHD source and real-world highlight quality remain to be measured. Build and launch instructions are in the README.
