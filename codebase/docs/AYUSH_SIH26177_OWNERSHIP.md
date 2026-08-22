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

## Reserved for the next accepted interface handoff

After Suyash/Samik publish the immutable rescue model-registry/class-map
artifact, Ayush may make bounded changes to:

- the registry-loading boundary used by `codebase/tools/covis_multicam.py`;
- its focused multi-camera tests;
- its example configuration and documentation.

That change must load the frozen model hash, modality, model ID, preprocessing,
thresholds and supported rescue classes without changing the existing 2-D
homography, `AGREE`/`DISPUTE`/`ABSTAIN`, create-once evidence or release contract.

## Explicitly not owned

- Samik's dataset download, conversion, training or evaluation files;
- raw datasets, `SOURCE.json`, weights, virtual environments or generated evidence;
- rescue ontology/event schema until its owner publishes the shared interface;
- Pratik's simulator/autonomy files;
- Suyash's dashboard/report pipeline; and
- Abhijan's scenario/attack assets.

Cross-lane edits require an explicit handoff from the owning human and a new
changed-file list before implementation.
