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

## Producer handoff

Samik emits `observation` events after local inference. Pratik emits mission, assignment,
coverage, vehicle, link, task-reassignment and mission-completion events. Suyash consumes
the projected state/report. Abhijan evaluates outputs against simulator truth after the
mission; truth is never a producer input.

Required top-level producer fields:

```json
{
  "schema": "veriswarm.rescue.event.v1",
  "mission_id": "OP-VARUNA-001",
  "event_id": "alpha-person-0007",
  "source": "alpha",
  "source_seq": 7,
  "observed_at_ms": 1787394601200,
  "kind": "observation",
  "payload": {}
}
```

`event_id` makes offline replay idempotent. Re-delivery of identical content is a successful
no-op. Reusing the ID with different content or sending a source sequence backwards is
rejected. A forward sequence gap is accepted with a warning so a temporarily disconnected
producer can recover without blocking the fleet.

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

## Projection behavior

- Nearby geolocated person observations are merged within a five-metre development gate,
  retaining every source and observation ID.
- Non-geolocated people stay separate because image-plane overlap does not prove identity.
- High-confidence or multi-source person candidates receive `HIGH`; others receive
  `REVIEW`.
- Mapped fire/smoke receives `CRITICAL`; other mapped hazards receive `HIGH`.
- `HOLD`/`QUARANTINE` authorization events become responder-visible safety alerts.
- Reports explicitly retain uncertainty and state that candidates require human review.

These are deterministic demo rules, not medically or operationally calibrated dispatch
policy.
