# Rescue mission data plane

This is Suyash's integration surface for SIH problem statement 26177. It extends the
existing append-only VeriSwarm JSONL event bus; it does not replace the model-hash,
receipt or supervisor events.

## Quick smoke test

From `codebase/`:

```bash
python -m tools.rescue_event_collector ingest \
  --mission-id OP-VARUNA-001 \
  --input examples/rescue_event_sample.jsonl \
  --log results/rescue_events.dev.jsonl

python -m tools.rescue_event_collector report \
  --mission-id OP-VARUNA-001 \
  --log results/rescue_events.dev.jsonl \
  --out results/rescue_report.dev.json
```

Development output may be deleted or overwritten. Final run retention begins only after
the feature freeze.

## HTTP mode

Local development:

```bash
python -m tools.rescue_event_collector serve \
  --bind 127.0.0.1 --port 8770 \
  --mission-id OP-VARUNA-001 \
  --log results/rescue_events.dev.jsonl
```

Endpoints:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/events` | validate and append one producer event |
| `GET` | `/health` | service and applied-event count |
| `GET` | `/state` | dashboard projection |
| `GET` | `/report` | responder-oriented report projection |

A non-loopback bind refuses to start without a 32-character token in
`VERISWARM_RESCUE_TOKEN`. Clients may send it as `Authorization: Bearer ...` or
`X-VeriSwarm-Token`.

## Offline producer outbox

Samik and Pratik must persist an event before attempting the network. Each producer uses
its own SQLite outbox; do not point multiple nodes at the same file.

Enqueue one JSON file, a JSON array, or JSONL events:

```bash
python -m tools.rescue_event_sender \
  --mission-id OP-VARUNA-001 \
  --outbox results/alpha-rescue-outbox.sqlite3 \
  enqueue --input examples/rescue_event_sample.jsonl
```

Deliver pending events after the collector becomes reachable:

```bash
python -m tools.rescue_event_sender \
  --mission-id OP-VARUNA-001 \
  --outbox results/alpha-rescue-outbox.sqlite3 \
  flush --endpoint http://127.0.0.1:8770
```

Inspect pending attempts and retained dead letters:

```bash
python -m tools.rescue_event_sender \
  --mission-id OP-VARUNA-001 \
  --outbox results/alpha-rescue-outbox.sqlite3 \
  status
```

The sender provides at-least-once delivery:

- validation happens before durable enqueue;
- exact event bytes and their internal content digest are retained in SQLite;
- FIFO replay preserves producer sequence order;
- network, service, endpoint and authentication failures remain pending;
- transient network/5xx retries are bounded inside one flush invocation;
- an exact collector duplicate is treated as delivered;
- a collector ordering/identity conflict is retained in the dead-letter table;
- local content corruption is quarantined before transmission.

`flush` returns exit code `0` only when the outbox is empty, `3` when valid events remain
pending, and `2` for invalid local input/configuration. A pending result is expected during
an offline interval and must not be converted into mission failure by the producer.

For a LAN collector, set `VERISWARM_RESCUE_TOKEN` in both collector and sender processes.
The token is sent in the HTTP authorization header and is never stored in the outbox.

## Producer handoff

Samik emits `observation` events after local inference. Pratik emits mission, assignment,
coverage, vehicle, link, movement-safety, task-reassignment and mission-completion events.
Suyash consumes the projected state/report. Abhijan evaluates outputs against simulator
truth after the mission; truth is never a producer input.

Required top-level producer fields:

```json
{
  "schema": "veriswarm.rescue.event.v1",
  "mission_id": "OP-VARUNA-001",
  "event_id": "alpha-person-0007",
  "source": "alpha.perception",
  "source_seq": 7,
  "observed_at_ms": 1787394601200,
  "kind": "observation",
  "payload": {}
}
```

`source` is a producer-stream identity. Use `alpha.perception` for Samik's detector,
`alpha.telemetry` for Pratik's vehicle/coverage/link producer and `alpha.fusion` for a
localized hazard promoter. Replace `alpha` with the subject node. This gives independent
processes independent `source_seq` counters without allowing one node's stream to claim
another node. The legacy exact-node form remains accepted for a single combined producer.

`event_id` makes offline replay idempotent. Re-delivery of identical content is a successful
no-op. Reusing the ID with different content or sending a source sequence backwards is
rejected. A forward sequence gap is accepted with a warning so a temporarily disconnected
producer can recover without blocking the fleet.

New FactoryCity cell/coverage/reassignment producers and measured obstacle/collision
events follow `RESCUE_CELL_MOVEMENT_EVENT_EXTENSION.md`. The extension is additive: old
count-only evidence remains replayable, while new producers must emit exact cell IDs.

## Observation contract

```json
{
  "node": "alpha",
  "observation_id": "alpha-track-7",
  "class_id": "person_candidate",
  "confidence": 0.87,
  "frame_id": "alpha-frame-104",
  "modality": "rgb",
  "model_id": "sar-alert-rgb-k0",
  "model_sha256": null,
  "bbox_norm": [0.31, 0.22, 0.48, 0.81],
  "position_ned": [42.1, 18.7, 0.0],
  "uncertainty_m": 3.2
}
```

`bbox_norm` is `[x1,y1,x2,y2]` in `[0,1]`. `position_ned` and `uncertainty_m` are optional;
without them the dashboard must show an image-space observation and must not invent a map
point. `model_sha256` may be null in development and is required only when a selected model
enters the final model-identity allowlist.

Accepted classes are `person_candidate`, `water_or_flood`, `road_blocked`, `debris`,
`fire`, `smoke`, `structure_damage` and `landslide`. No event may call a person a confirmed
survivor.

Calibrated multi-view producers may also include:

```json
{
  "capture_group_id": "building-7-person-1",
  "camera_id": "camera-b",
  "camera_calibration_id": "factorycity-camera-b-v1",
  "viewpoint_ned": [0.0, 0.0, 0.0],
  "bearing_ned": [0.0, 0.0, 1.0],
  "bearing_uncertainty_deg": 0.5,
  "localization_method": "metric_range",
  "metric_range_m": 10.0,
  "range_uncertainty_m": 0.3,
  "corroborated_sources": ["bravo"],
  "expected_visible_misses": [],
  "evidence_security": "VERIFIED",
  "security_reasons": []
}
```

See `MULTIVIEW_SURVIVOR_FUSION.md`. The capture group is produced only by tracking,
calibrated geometry, simulator identity or another measured association method; raw box
IoU across unregistered viewpoints is not sufficient.

`evidence_security` is not detector-supplied trust. The producer uses
`rescue.assess_consensus_security(...)` to bind the real VeriSwarm consensus result to the
expected receipt digest plus receipt/model/runtime verification. A positive person event
is still sent when that result is `UNVERIFIED` or `DISPUTED`; the state controls the
security-review alert and autonomous-control path, not whether responders see the person.

## Projection behavior

- Nearby geolocated person observations are merged within a five-metre development gate,
  retaining every source and observation ID.
- Non-geolocated people stay separate because image-plane overlap does not prove identity.
- Every detector-thresholded person candidate receives `HIGH` responder priority. Missing
  views never veto it; every candidate remains explicitly unconfirmed.
- Calibrated positive views merge only when both their association group and measured NED
  positions agree. A capture-group ID or spatial proximity alone cannot merge them. Bearing
  evidence is retained and the most precise available NED location is preferred.
- A disputed positive view or independently proven expected-visible miss adds a separate
  security-review alert without removing the person alert.
- Mapped fire/smoke receives `CRITICAL`; other mapped hazards receive `HIGH`.
- `HOLD`/`QUARANTINE` authorization events become responder-visible safety alerts.
- Terminal cell blockage and en-route collision events become responder-visible movement
  safety alerts; measured transition evidence remains available in `movement_safety`.
- Reports explicitly retain uncertainty and state that candidates require human review.

These are deterministic demo rules, not medically or operationally calibrated dispatch
policy.
