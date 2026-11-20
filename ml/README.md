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
FRAMEFOLD_RANKER=data/ranker python3 -m framefold.server
```

The training command stratifies by family and partitions duplicate groups before constructing source-plus-brief ranking queries. It saves the model, feature schema, seed, split assignments, dataset and model checksums, baseline and ranker nDCG@3, human acceptance among the top three, and the count of held-out queries with an accepted pick. Close off the test labels during feature development and use a separate independent set before making a public performance claim. Freeze the dataset and split manifest for each comparison.

## Model status

No rights-cleared, human-annotated production corpus was supplied. The local app uses the rules selector unless a compatible trained model is explicitly configured. There are no production per-family metrics or published weights. The baseline can miss quiet actions, rapid back-to-back events, and semantically important speech without a pause or sentence boundary. It can also score camera motion and edits as action. The optional ranker inherits the proposal stage's coverage limits.
