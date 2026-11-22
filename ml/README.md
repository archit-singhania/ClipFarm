# Local highlight ranker

The default selector uses deterministic motion, audio and sentence boundaries. Feature masks distinguish audio availability from measured energy and transcript availability from candidate speech fraction. A trained ranker may reorder the same candidates. Candidate filtering and deduplication run after either score path.

## Candidate labels

Use videos you have permission to analyze. Record the source identifier, family, duplicate group, creative brief and permission or license in each row. Keep versions of an original, alternate cuts and re-encodes in the same duplicate group. Proposed families are interview, sports, gaming, tutorial, music, silent, vlog and animation.

```sh
python3 -m ml.ranker candidates data/RUN_ID/analysis.json labels-source.jsonl \
  --duration-ms 60000 --source tutorial-001 --duplicate-group tutorial-001 \
  --family tutorial --rights 'Original recording, training approved'
```

| Label | Human judgment |
| --- | --- |
| 0 | Misleading, unusable, or lacks a recognizable moment |
| 1 | Relevant event but the cut misses necessary context or its outcome |
| 2 | Coherent, self-contained, usable pick |
| 3 | Complete and compelling pick with strong boundaries |

For example, a shot where the ball enters the goal with no buildup can receive a 1. The same event with the setup and result can receive a 2. Disagreement about pacing versus missing context should be adjudicated before merging labels. Keep independent highlight annotations to measure candidate recall at temporal IoU ≥ 0.5.

## Training and serving

```sh
python3 -m pip install -r ml/requirements.txt
python3 -m ml.ranker train labeled-candidates.jsonl data/ranker
CLIPFARM_RANKER=data/ranker python3 -m framefold.server
```

The training command stratifies by family and partitions duplicate groups before constructing source-plus-brief ranking queries. It saves the model, feature schema, seed, split assignments, dataset and model checksums, baseline and ranker nDCG@3, human acceptance among the top three, and the count of held-out queries with an accepted pick. Close off the test labels during feature development and use a separate independent set before making a public performance claim. Freeze the dataset and split manifest for each comparison.

## Review candidates locally

After exporting one source's candidate JSONL, create a local review page beside the source. The page plays each candidate's time range and collects a 0–3 creator rating. It reveals the range and source name while hiding the model score. Download the judgments before closing the page. The command preserves the original media.

```sh
python3 -m ml.review labels-source.jsonl original.mp4 review.html
```

Open the generated HTML file locally. Combine reviewed originals into one JSONL and use the training command above. Adjudicate multiple reviewers' scores for the same source and range before training. Keep ratings from the held-out source groups independent of feature or threshold decisions.

## Bundled film model

The Film trailer pilot uses real source footage and frame matches with human-edited teasers from Blender's open movie projects. The weights, training rows, provenance, held-out results and reproducible commands are in the [model card](editorial/MODEL_CARD.md). The portable scorer uses standard Python at runtime. Select the film option on a run or set `CLIPFARM_RANKER=editorial` for an operator default. An explicit rules choice overrides that environment setting.

A broader model still needs direct creator evaluations of complete clips across content families. The baseline can miss quiet actions, adjacent events, and meaningful speech without a pause. Camera motion and edits can also resemble action. The film ranker inherits the same candidate coverage.
