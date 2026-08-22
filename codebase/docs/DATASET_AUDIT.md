# Independent YOLO dataset audit

`tools.audit_yolo_dataset` is Ayush's independent SIH 26177 perception-support
tool. It does not download data, convert Samik's sources, train models or modify
any image/label file.

It verifies:

- recursive image/label pairing by relative stem;
- readable images and valid dimensions;
- exactly five YOLO detection fields per non-empty label line;
- finite, normalized, in-frame boxes with supported class IDs;
- negative images represented by empty label files;
- SHA-256-identical images within a split; and
- hard cross-split leakage from byte-identical images.

It creates a new output directory containing `audit_report.json` and a
deterministic stratified `label_montage.jpg` of up to 100 samples. An existing
output directory is never overwritten. Cross-split duplicates, missing/orphan
labels, unreadable images and invalid boxes make the command exit nonzero.
Within-split duplicate bytes are visible warnings for independent review.

The explicit class map is JSON:

```json
{
  "0": "person_candidate",
  "1": "fire",
  "2": "smoke"
}
```

Class IDs must be contiguous from zero. Use only the frozen converter/model
class map; do not infer rescue labels from a dataset folder name.

Example from `codebase`:

```powershell
python -m tools.audit_yolo_dataset `
  --split train "D:\private-data\images\train" "D:\private-data\labels\train" `
  --split val "D:\private-data\images\val" "D:\private-data\labels\val" `
  --split test "D:\private-data\images\test" "D:\private-data\labels\test" `
  --class-map "D:\private-data\class_map.json" `
  --output-dir "D:\private-audits\disaster-response-v1" `
  --montage-size 100
```

Keep raw data, montage output and audit reports outside Git. Report the audit
command, converter commit, source archive SHA-256, class map hash, counts,
leakage result and montage path to Samik/Suyash. Byte hashing catches exact
duplicates only; near-duplicate scene/video leakage still requires the planned
grouped-split/provenance review.
