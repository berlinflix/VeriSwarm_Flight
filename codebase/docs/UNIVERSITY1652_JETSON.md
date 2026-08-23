# University-1652 Jetson visual relocalization

This integration treats University-1652 retrieval as a **coarse appearance sensor**. It
does not replace VIO/IMU, produce continuous metric pose, control the aircraft, or prove
the drone's position from one match. Depth/LiDAR continues to own obstacle safety.

## Frozen downloaded artifacts

- Dataset archive: `University-Release.zip`
  - size: `9204115162` bytes
  - SHA-256: `8a6068a93ef1edaa70535ef8a37cdd853956f9d1757eca7ec703827f473e7d2f`
- Model archive: `university1652-model.zip`
  - size: `1292474874` bytes
  - SHA-256: `5e523363f4ce82aa0db20945ccbc1780de810f65d7223fee14948d8f9e2546ca`
- Selected checkpoint:
  `three_view_long_share_d0.75_256_s1_google/net_119.pth`
  - size: `210697045` bytes
  - SHA-256: `7a86d1e0be58caa27bd7945b86c750239b9211e178143642e8c316aa5ce14281`

The archive's `usa_vgg_noshare_warm5_lr2` checkpoint is for CVUSA and must not be
installed as the University-1652 drone encoder.

## Data placement

Keep the 9.2 GB source archive and extracted training set off the Jetson. Transfer only:

1. the selected 210 MB checkpoint;
2. the satellite gallery required for the chosen operating area;
3. the generated compressed gallery embedding cache; and
4. the gallery-to-coordinate manifest for operational use.

The included University benchmark gallery identifies dataset building IDs. It does not
contain the current CoSys world or an arbitrary real deployment area. For CoSys, render a
top-down tile gallery and associate each tile with known NED coordinates. For a real
deployment, prepare licensed/georeferenced satellite or orthophoto tiles offline.

## Benchmark probe

Run from `codebase` in the JetPack rescue environment. The first invocation embeds the
gallery and creates the cache; later invocations verify and reuse it.

```bash
source ~/.venv-rescue-jp621/bin/activate

python -m tools.university1652_retrieval \
  --checkpoint ~/VeriSwarm_Models/geolocation/university1652/net_119.pth \
  --expected-checkpoint-sha256 7a86d1e0be58caa27bd7945b86c750239b9211e178143642e8c316aa5ce14281 \
  --gallery-dir ~/VeriSwarm_Geolocation/university1652/gallery_satellite \
  --gallery-cache ~/VeriSwarm_Geolocation/university1652/gallery-951.fp16.npz \
  --query ~/VeriSwarm_Geolocation/university1652/query_drone/0038/image-01.jpeg \
  --top-k 5 \
  --out ~/VeriSwarm_Jetson_Evidence/geolocation/u1652-0038-01.json
```

With no thresholds supplied, the result deliberately reports `accepted: false` and
`reason: thresholds_not_configured` while still returning top-k candidates. Thresholds
must be selected on a validation set for the actual gallery/domain, not copied from the
University benchmark.

Validate every labelled drone view in a directory before trusting the integration. Use a
small batch on the 8 GB Nano to avoid transient NvMap allocation pressure:

```bash
python -m tools.university1652_validate \
  --checkpoint ~/VeriSwarm_Models/geolocation/university1652/net_119.pth \
  --expected-checkpoint-sha256 7a86d1e0be58caa27bd7945b86c750239b9211e178143642e8c316aa5ce14281 \
  --gallery-dir ~/VeriSwarm_Geolocation/university1652/gallery_satellite \
  --gallery-cache ~/VeriSwarm_Geolocation/university1652/gallery-951.fp16.npz \
  --query-dir ~/VeriSwarm_Geolocation/university1652/query_drone/0038 \
  --batch-size 4 \
  --top-k 10 \
  --out ~/VeriSwarm_Jetson_Evidence/geolocation/u1652-0038-validation.json
```

The report separates per-frame top-1/top-5 accuracy from offline temporal descriptor
fusion. A fused match remains an appearance hypothesis and cannot directly reset pose.

## Operational acceptance sequence

An appearance match may become a global correction only after all of the following:

1. frozen model and gallery identities match;
2. top-1 similarity passes the domain-specific threshold;
3. top-1/top-2 margin passes its threshold;
4. the same area is supported across a temporal window;
5. the displacement and velocity are feasible under VIO/IMU;
6. map bounds and altitude/scale assumptions are satisfied; and
7. the correction is fused with covariance rather than overwriting pose.

When any requirement fails, emit `VISUAL_FIX_UNAVAILABLE`; keep VIO active and never
jump the vehicle to the first retrieved tile.
