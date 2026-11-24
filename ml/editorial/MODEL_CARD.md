# ClipFarm film trailer pilot

An optional local LightGBM LambdaRank model learns from two independent open movies and their human-edited trailers. Its training target is the amount of trailer footage that a candidate contains. This is indirect editorial supervision. Direct creator judgments of coherence, shareability and caption quality are still needed for a general production ranker.

## Footage and editorial choices

| Original | Attribution | Source rights |
| --- | --- | --- |
| Big Buck Bunny | © 2008 Blender Foundation / www.bigbuckbunny.org | [Peach project rights](https://peach.blender.org/about/) · [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| Tears of Steel | Blender Foundation / mango.blender.org | [Mango project rights](https://mango.blender.org/about/) · [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |

The source manifest records the media URLs, release archive members and SHA-256 digests. ClipFarm derives numeric features and timing labels by comparing these works. This directory contains the derived feature rows, time spans and weights. Original film and trailer files stay in the chosen local cache.

The official TVSum archive describes CC BY video footage and separately requires a signed academic data-sharing agreement for the annotation collection. The TVSum ratings were excluded. Elephants Dream and the first four minutes of Sintel produced no matches under the temporal alignment criteria and contributed zero training rows.

## Labels and coverage

The matching code samples both edits twice per second, crops persistent black borders, removes low-contrast frames, and compares normalized image grids. Three consecutive aligned samples must pass the documented correlation and ambiguity thresholds. Only matched spans count as positive evidence. Some shots are missed when color grading, alternate camera angles, timing or animation differ between the teaser and the final film. A zero means no detected overlap.

ClipFarm generates candidate windows with the same production motion and audio rules used for delivery. Training additionally uses 5.5-second windows at two-second intervals to cover shorter edits. The grade is 0 for zero matching time, 1 for a positive overlap below 10%, 2 for an overlap of at least 10%, and 3 for an overlap of at least 25%. ASR is disabled for this self-contained corpus build. The exported candidate data identifies the type of every window and the number of matched milliseconds.

## Evaluation and intended use

For each fold, all windows of one film and its trailer are held out together. Only original generator proposals enter the evaluation. nDCG@3 measures ranking before the final overlap and diversity filter. Every extra training window from the held-out original is excluded. The shipped weights are refit on the available originals after evaluation.

The final dataset contains 780 candidate windows, 41 of which overlap confirmed teaser footage. Seven matched source intervals account for 18.5 seconds of footage. FFmpeg 8.1.2 extracted the signals and LightGBM 4.6.0 fitted the 30-round booster. Each fold includes all eligible held-out production proposals, including those assigned grade 0.

| Held-out original | Eligible proposals | Proposals overlapping a matched shot | Rules nDCG@3 | Trained nDCG@3 |
| --- | ---: | ---: | ---: | ---: |
| Big Buck Bunny | 63 | 2 | 0.0000 | 0.0000 |
| Tears of Steel | 56 | 13 | 0.2346 | 0.0000 |
| Mean across originals | | | 0.1173 | 0.0000 |

The model missed all labeled shots among its top three candidates on both held-out films. The deterministic rules found one on Tears of Steel. These results support retaining the rules profile as the default. ClipFarm exposes the learned model for reproducible experiments.

The corpus contains two films made in one studio. Scores are sensitive to a handful of matched shots. The live-action/VFX film and animated film provide no independent evidence for tutorials, podcasts, sports, music, games or creator acceptance. Use the rules profile for general work. Treat the Film trailer profile as experimental.

## Reproduction

The versioned JSONL allows offline reproduction of training. Full feature regeneration additionally downloads the source movies and teasers. Start from the repository root, use the pinned Python packages and the FFmpeg version below, and point the cache at a directory with approximately 2 GB free.

```sh
python3 -m pip install -r ml/requirements.txt
python3 -m ml.train_editorial ml/editorial/candidates.jsonl /tmp/clipfarm-trained
python3 -m ml.editorial /tmp/clipfarm-media /tmp/candidates.jsonl /tmp/matches.json
python3 -m ml.train_editorial /tmp/candidates.jsonl /tmp/clipfarm-refit
python3 -m unittest discover -s tests -p test_editorial_ranker.py -v
```

Training writes the native booster, a portable numeric split tree, and their checksums. The export compares portable and native predictions on every training row at a tolerance of 1e-12. Runtime verifies weights, feature order and digests. The bundled scoring path requires no model download or optional Python packages.
