# Ayush SIH 26177 perception-support ownership

**Branch:** `ayush/sih26177-perception-support`

**Initial base:** `69041b020db3afad5cabead314ea50beea2b9524`

## Owned in the first deliverable

- `codebase/tools/audit_yolo_dataset.py`
- `codebase/tests/test_audit_yolo_dataset.py`
- `codebase/docs/DATASET_AUDIT.md`
- this ownership file

The tool reads private dataset directories but never edits them. Generated
montages and audit JSON remain outside Git.

## Second milestone: frozen registry adapter

Implemented independently against the frozen `veriswarm.model.v1` interface in the
SIH 26177 plan:

- `codebase/tools/rescue_model_registry.py`
- `codebase/tools/covis_multicam.py` registry-loading boundary
- `codebase/tests/test_rescue_model_registry.py`
- focused additions to `codebase/tests/test_covis_multicam.py`
- `codebase/config/rescue_model_registry.example.json`
- `codebase/docs/RESCUE_MODEL_REGISTRY.md`

The adapter validates the model hash, modality, model ID, preprocessing declaration,
registry hashes and ordered supported rescue classes. It does not change the existing
2-D homography, `AGREE`/`DISPUTE`/`ABSTAIN`, create-once evidence or release contract.
The final selected registry/model bytes are still an external integration input and are
not committed.

## Explicitly not owned

- Samik's dataset download, conversion, training or evaluation files;
- raw datasets, `SOURCE.json`, weights, virtual environments or generated evidence;
- rescue ontology/event schema until its owner publishes the shared interface;
- Pratik's simulator/autonomy files;
- Suyash's dashboard/report pipeline; and
- Abhijan's scenario/attack assets.

Cross-lane edits require an explicit handoff from the owning human and a new
changed-file list before implementation.
