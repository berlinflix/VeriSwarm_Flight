# VeriSwarm University-1652 visual geolocation console

This console accepts one drone-view image, executes the frozen University-1652
cross-view retrieval model on the Jetson, ranks the satellite gallery and maps
the official WGS84 coordinate of each candidate.

It is a **coarse visual relocalization sensor**, not GPS. The map marker is the
coordinate of the matched gallery building. It must remain an unverified visual
hypothesis until calibrated appearance thresholds and temporal VIO/IMU
consistency checks pass.

## Map provider and API key

The default uses Leaflet and OpenStreetMap, so no API key is required. The
browser displaying the console needs Internet access to retrieve map tiles.

Google Maps is optional. Supply a browser-restricted key at launch without
storing it in Git:

```bash
export GOOGLE_MAPS_API_KEY='replace-with-browser-restricted-key'
```

Restrict the key in Google Cloud to the Maps JavaScript API and to the demo
origin/IP wherever the provider permits. Browser map keys are visible to the
browser by design; never use an unrestricted server credential.

## Jetson launch

Only one service can use TCP port `8080`. Stop `rescue_console.py` before this
launch, or run this service on `8081`.

```bash
source ~/.venv-rescue-jp621/bin/activate

python ~/jetson_handoff_758a177/code/university1652_console.py \
  --checkpoint ~/jetson_handoff_758a177/model/net_119.pth \
  --expected-checkpoint-sha256 7a86d1e0be58caa27bd7945b86c750239b9211e178143642e8c316aa5ce14281 \
  --gallery-dir ~/jetson_handoff_758a177/data/gallery_satellite \
  --gallery-cache ~/jetson_handoff_758a177/gallery-951.fp16.npz \
  --locations ~/jetson_handoff_758a177/code/university1652_locations.json \
  --rescue-model ~/VeriSwarm_Jetson_Offline/wheelhouse/yolov8n.pt \
  --expected-rescue-model-sha256 f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36 \
  --rescue-stride 10 \
  --rescue-max-frames 400 \
  --evidence-dir ~/VeriSwarm_Jetson_Evidence/geolocation-console \
  --rescue-evidence-dir ~/VeriSwarm_Jetson_Evidence/rescue-video \
  --bind 0.0.0.0 \
  --port 8080
```

Use one process and one port:

- `http://192.168.50.10:8080/rescue` — disaster-video survivor detection.
- `http://192.168.50.10:8080/` — University-1652 visual geolocation.
- `http://192.168.50.10:8080/hazards` — India hazard atlas.

The detector and geolocation encoder load lazily. A single GPU lock serializes
them; starting one workflow unloads the other model first. The hazard atlas
loads neither model. Do not run the old standalone `rescue_console.py` at the
same time because only one process can bind TCP port `8080`.

Open `http://192.168.50.10:8080/hazards` for the India Rescue Coverage Atlas.
It provides interactive flood, seismic, landslide and multi-hazard planning
overlays, a satellite basemap, downloadable regional acquisition briefs and
the supplied reference-map gallery. The atlas does not load the University-1652
model and therefore does not consume model GPU memory.

The colored atlas geometry is **illustrative, not operational GIS**. Before
deployment, replace it with authoritative machine-readable layers from
NRSC/Bhuvan and GSI Bhusanket/Bhukosh (plus the applicable NDMA/BIS/CWC
sources). Static reference screenshots are displayed as reference material;
the console does not claim they are live or machine-readable government data.

The model and gallery cache are loaded lazily on the first upload. Press
**Unload model** in the console to release model references and cached CUDA
memory when the geolocation beat ends.

## Judge-demo image selection

Run this once on the Jetson after the console bundle is installed. It evaluates
every available view from location `0038`, retains only top-1-correct results,
and creates a reproducible demo folder. It does not retrain or modify the model.

```bash
source ~/.venv-rescue-jp621/bin/activate

python ~/jetson_handoff_758a177/code/select_university1652_demo.py \
  --checkpoint ~/jetson_handoff_758a177/model/net_119.pth \
  --expected-checkpoint-sha256 7a86d1e0be58caa27bd7945b86c750239b9211e178143642e8c316aa5ce14281 \
  --gallery-dir ~/jetson_handoff_758a177/data/gallery_satellite \
  --gallery-cache ~/jetson_handoff_758a177/gallery-951.fp16.npz \
  --locations ~/jetson_handoff_758a177/code/university1652_locations.json \
  --query-dir ~/jetson_handoff_758a177/data/query_drone/0038 \
  --expected-location-id 0038 \
  --out-dir ~/VeriSwarm_U1652_JUDGE_DEMO_0038 \
  --count 6 \
  --batch-size 8
```

Copy the create-once folder to the operator laptop:

```powershell
scp -r "akaberlinflix@192.168.50.10:/home/akaberlinflix/VeriSwarm_U1652_JUDGE_DEMO_0038" `
  "C:\Users\suyas\Downloads\"
```

`DEMO_MANIFEST.json` records the expected building, official coordinate, model
identity and pre-measured retrieval scores. The README explicitly distinguishes
a known-example demonstration from a field-accuracy estimate.

## API

- `GET /health` or `GET /api/status`: runtime state and identities.
- `GET /hazards`: judge-facing India hazard/reference-bank planning atlas.
- `GET /assets/hazards/<allow-listed-name>`: bundled reference images.
- `GET /rescue`: disaster-video survivor-detection console.
- `POST /api/rescue/analyze`: start a create-once video-analysis job.
- `GET /api/rescue/jobs/<UUID>`: retrieve job progress and signed sightings.
- `POST /api/rescue/unload`: release the detector and CUDA cache.
- `POST /api/analyze`: multipart form upload field `image`.
- `POST /api/unload`: release the encoder, gallery matrix and CUDA cache.
- `GET /api/gallery/<four-digit-id>`: safe satellite thumbnail for a candidate.

Each successful request writes a unique query image and JSON result beneath the
configured evidence directory. Inputs are never overwritten.
