# Kaggle disaster-data shortlist and fast-start decision

## Decision

Kaggle is approved for rapid private hackathon experimentation and cloud training. A
missing or unclear licence is no longer a download blocker. It is still a release blocker:
record the Kaggle slug, owner, version/date, URL, archive SHA-256 and the terms displayed
at retrieval. Do not commit raw data, re-upload a restricted dataset, publish its samples,
or release weights trained from unclear/restrictive data until Suyash reviews the terms.

This relaxes paperwork latency; it does not make unlicensed material ours.

## Download and training order

| Priority | Kaggle source | Usable task | Decision and limitation |
|---|---|---|---|
| K0 | [Disaster Response Object Detection Dataset](https://www.kaggle.com/datasets/rupankarmajumdar/disaster-response-object-detection-dataset) | YOLO boxes for person, fire, smoke, small/large vehicle and two-wheeler | Download first. Train `sar-alert-rgb-k0` as the quickest end-to-end person/fire/smoke prototype. Audit source overlap, duplicates and aerial-domain coverage before accepting any metric. A `person` box is a `person_candidate`, never proof of a survivor. |
| K0 | [Smoke-Fire Detection YOLO](https://www.kaggle.com/datasets/sayedgamal99/smoke-fire-detection-yolo/data) | roughly 21k YOLO images with fire/smoke boxes and negative images | Download in parallel. Use as the first fire/smoke specialist. It is not an aerial-disaster qualification; test separately on aerial/simulator frames. |
| K1 | [Fire/Smoke Detection YOLO v9](https://www.kaggle.com/datasets/roscoekerby/firesmoke-detection-yolo-v9) | more than 35k labelled fire/smoke images | Alternative fire/smoke source if K0 conversion fails or an independent cross-dataset test is needed. Do not mix it into K0 until class and duplicate audits pass. |
| K1 | [Disaster Damage 5-Class](https://www.kaggle.com/datasets/sarthaktandulje/disaster-damage-5class) | about 9k fire/flood/smoke/landslide/earthquake/normal scene images | Auxiliary scene classifier only. It cannot draw hazard boxes or polygons. Landslide/smoke are imbalanced; use macro metrics and class-balanced sampling. |
| K1 | [Thermal Image People Detection](https://www.kaggle.com/datasets/kausthubkannan/thermal-image-people-detection) | thermal human boxes | Fallback only if HIT-UAV access is slow. Accept only after a 100-image annotation/domain audit and train/test provenance check. |
| K2 | [WildFire Smoke YOLO](https://www.kaggle.com/datasets/ahemateja19bec1025/wildfiresmokedatasetyolo/versions/2) | wildfire-smoke YOLO labels | Useful smoke-domain cross-test or specialist augmentation after duplicate/source audit. |
| K2 | [Fire and Smoke Object Detection YOLO](https://www.kaggle.com/datasets/azimjaan21/fire-and-smoke-dataset-object-detection-yolo) | mixed real/CCTV/synthetic fire-smoke boxes | Auxiliary only. Preserve source-domain tags and never let synthetic/CCTV images leak into the aerial test claim. |
| K3 | [Cyclone, Wildfire, Flood, Earthquake Database](https://www.kaggle.com/datasets/rupakroy/cyclone-wildfire-flood-earthquake-database) | disaster scene classification | Exploratory baseline only. The page acknowledges web collection, noise, duplication and imbalance; never use its test split as the sole acceptance evidence. |
| K3 | [Disasters Dataset](https://www.kaggle.com/datasets/georgemystriotis/disasters-dataset) | combined fire/flood/earthquake/neutral scene classification | Exploratory only. It combines other Kaggle sources and has provenance/duplicate risk. Do not treat it as object detection. |

## Parallel cloud jobs

Start these as separate jobs so one bad corpus cannot contaminate every capability:

1. **Job A — rapid integrated detector:** K0 Disaster Response, YOLOv8n, five-epoch smoke
   run followed by a bounded full run. Remap `person` to `person_candidate`; retain fire and
   smoke. Keep vehicle outputs optional and outside the rescue acceptance gate.
2. **Job B — fire/smoke specialist:** K0 Smoke-Fire YOLO, YOLOv8n then `s` only if the
   timing budget passes. Include negative images and measure false alarms.
3. **Job C — aerial person specialist:** official VisDrone/HERIDAL, as defined in the main
   dataset plan. This is the aerial-domain anchor.
4. **Job D — thermal person specialist:** official HIT-UAV; Kaggle thermal is fallback.
5. **Job E — flood/access segmentation:** official FloodNet. A scene classifier is not a
   substitute for a map polygon.

Do not concatenate Jobs A–E into one dataset unless every image has been exhaustively
labelled for every retained class. Missing labels otherwise become false background.

## Fast dataset rule

Each download receives `SOURCE.json` before training, but this is a ten-minute operation,
not a legal-review queue. Required now:

```text
dataset_id and Kaggle slug/URL
Kaggle owner and visible version/date
retrieval timestamp UTC
archive filename, bytes and SHA-256
terms/licence exactly as displayed, or TERMS_NOT_DISPLAYED
allowed use state: PRIVATE_HACKATHON_EXPERIMENT
raw and converted counts
class mapping and converter commit
split method and duplicate/leakage report
```

Use three acceptance states:

- `ACCEPTED_DEMO_INTERNAL`: may be trained/evaluated privately; source and integrity are
  recorded; it is not redistributed.
- `ACCEPTED_RELEASE`: terms and provenance are reviewed and permit the intended release.
- `EXPLORATORY_ONLY`: terms, labels, provenance or domain are insufficient for a product
  claim; results may guide development but cannot be the final evidence.

## Minimum checks before spending GPU time

- open 20 positive and 20 negative images;
- parse every annotation and reject invalid/off-image boxes;
- render a stratified 100-image montage;
- hash images and detect cross-split duplicates;
- group near-duplicate video/scene frames before splitting;
- record per-class counts and empty-label counts;
- reserve an untouched test split before the first run; and
- state whether imagery is aerial, ground, CCTV, synthetic or mixed.

Download speed does not justify skipping these checks: a five-epoch run on a leaking or
mislabelled split is slower than auditing it first.
