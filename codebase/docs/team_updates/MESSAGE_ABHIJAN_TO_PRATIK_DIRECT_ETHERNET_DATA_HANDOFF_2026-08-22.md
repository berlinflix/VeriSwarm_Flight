# MESSAGE — ABHIJAN TO PRATIK: DIRECT ETHERNET DATA HANDOFF

Date: 2026-08-22
From: Abhijan
To: Pratik, Suyash
Branch: `codex/abhijan-rescue-security`

## Decision

Pratik's CoSys telemetry goes directly from the Windows simulator PC to the rescue
collector on Abhijan's Mac. Suyash's laptop is not a runtime relay.

```text
Pratik CoSys PC (.11)
  -> source-IP-restricted Ethernet ingress on Abhijan's Mac (.14:8771)
  -> loopback-only canonical collector (127.0.0.1:8770)
  -> Vite server-side bridge
  -> Abhijan's browser dashboard (127.0.0.1:5175)
```

This does not change `veriswarm.rescue.event.v1`. Suyash remains the schema owner. The
Ethernet adapter is transport-only: the existing collector still validates, de-duplicates,
orders, retains and projects every event.

No rescue bearer token is shared. The ingress binds one exact Mac Ethernet address and
accepts `POST /events` only when the TCP source equals Pratik's configured `.11` address.
This is a demo-LAN restriction, not cryptographic producer authentication, and must not be
described as tamper-proof or suitable for an untrusted network.

## Address check

The frozen private topology is mandatory: `192.168.50.11` for Pratik and
`192.168.50.14` for Abhijan. The previously reported `192.158.50.11` is not an RFC1918
private address and must not be used for this tokenless demo transport. Both launchers
refuse any other address. Configure the two wired adapters with the frozen static
addresses before starting either script.

One-time adapter configuration:

- Pratik Windows Ethernet IPv4: address `192.168.50.11`, mask `255.255.255.0`,
  gateway blank, DNS blank.
- Abhijan Mac Ethernet IPv4: manual address `192.168.50.14`, subnet mask
  `255.255.255.0`, router blank.
- Keep Internet access on a separate adapter. Do not bridge or share Internet onto this
  demo link.

The Mac launcher also refuses a route through macOS Wi-Fi `en0`.

## Pratik: one-command sender

Abhijan first starts the simulation-only path on the Mac:

```bash
cd /Volumes/HyperDrive/Development/VERISWARM_SIH_RESCUE_SECURITY
./ops/start_abhijan_pratik_mac.sh
```

This launcher starts only the rescue collector, source-locked Ethernet ingress and
dashboard. It does not contact Jetson Alpha, Bravo, Charlie or Suyash's laptop, and it
does not start the model-hash qualifier. Use `ops/start_abhijan_rescue_mac.sh` only for a
later combined presentation in which both independent data planes must be visible.

After fetching this branch, run from PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\ops\start_pratik_rescue_sender.ps1
```

The script:

1. verifies that this PC owns the wired address `192.168.50.11`;
2. selects the frozen Mac address `192.168.50.14`;
3. verifies the ingress reports this exact Pratik source IP;
4. removes any old `VERISWARM_RESCUE_TOKEN` from the sender process;
5. continuously drains every `codebase/results/*-rescue-outbox.sqlite3` file; and
6. preserves pending events whenever Abhijan's Mac is offline.

The controller must enqueue before attempting network delivery. Use one SQLite file per
producer stream, for example:

```text
codebase/results/mission-controller-rescue-outbox.sqlite3
codebase/results/alpha-rescue-outbox.sqlite3
codebase/results/bravo-rescue-outbox.sqlite3
codebase/results/charlie-rescue-outbox.sqlite3
codebase/results/delta-rescue-outbox.sqlite3
codebase/results/echo-rescue-outbox.sqlite3
```

Do not share one SQLite file across independent producer identities.

## Required envelope for every event

```json
{
  "schema": "veriswarm.rescue.event.v1",
  "mission_id": "OP-VARUNA-001",
  "event_id": "alpha-state-000042",
  "source": "alpha.telemetry",
  "source_seq": 42,
  "observed_at_ms": 1787418000000,
  "kind": "vehicle_state",
  "payload": {}
}
```

Rules:

- `event_id` is globally unique and must never be reused for different content.
- `source_seq` starts at `1` and increases monotonically per exact `source`.
- `observed_at_ms` is the real transition/capture time in Unix milliseconds.
- Node telemetry sources are `<node>.telemetry`.
- Persist the validated event in the correct outbox before network delivery.
- Never invent GPS, NED position, sensor distance, detection or coverage.

## Data Pratik must emit

| Kind | When | Required payload | Dashboard result |
|---|---|---|---|
| `mission_started` | once after the live CoSys mission actually starts | `scenario_id`, `coordinate_frame: "NED"` | mission becomes ACTIVE |
| `assignment` | initial assignment and every reassignment result | `node`, `sector_id`, `cells_total`, exact `cell_ids` | per-drone owned cells |
| `vehicle_state` | on state/position change and at the agreed telemetry rate | `node`, `state`; genuine `position_ned` and `battery_pct` when measured | five cards and live map markers |
| `link_state` | on link transition | `node`, `state: ONLINE/DEGRADED/OFFLINE` | card link state |
| `coverage` | whenever a cell state changes | `node`, `sector_id`, `visited_cells`, `total_cells`, all three exact cell-ID lists | live heatmap and per-drone analysis |
| `movement_safety` | obstacle/hold/deflection/rejoin/block/collision transition | fields frozen below | movement-safety ledger and alerts |
| `task_reassigned` | after an unavailable owner loses unfinished work | `from_node`, `to_node`, `cells_count`, exact `cell_ids`, `reason` | ownership moves visibly |
| `mission_completed` | once after landing/cleanup result is known | `status`, `completed_cells`, `total_cells` | final mission state/report |

Allowed vehicle states:

```text
READY SEARCHING HOLD QUARANTINED LANDING LANDED FAILED
```

Every new FactoryCity coverage event must include all three pairwise-disjoint lists:

```json
{
  "node": "alpha",
  "sector_id": "route-alpha",
  "visited_cells": 1,
  "total_cells": 2,
  "in_progress_cell_ids": ["route_cell_05"],
  "completed_cell_ids": ["route_cell_00"],
  "blocked_cell_ids": []
}
```

`visited_cells` equals the length of `completed_cell_ids`. A cell is completed only after
its configured end boundary is crossed and its sensing/evidence is durably enqueued.

## Frozen movement-safety payloads

| `event_type` | `reason_code` | `result` |
|---|---|---|
| `OBSTACLE_DETECTED` | `depth_below_stopping_boundary` | `NON_TERMINAL` |
| `SAFETY_HOLD` | `obstacle_safety_hold` | `NON_TERMINAL` |
| `DEFLECTION_SELECTED` | `safe_deflection_selected` | `NON_TERMINAL` |
| `ROUTE_REJOINED` | `nominal_route_rejoined` | `NON_TERMINAL` |
| `CELL_BLOCKED` | `no_safe_deflection` | `TERMINAL_CELL` |
| `COLLISION_DETECTED` | `cosys_collision_detected` | `TERMINAL_VEHICLE` |

Each payload also requires `node`, `cell_id`, `measurement_source` and
`measured_distance_m`. Allowed measurement sources are:

```text
front_depth
front_depth_and_vehicle_state
simGetCollisionInfo
simGetCollisionInfo_and_vehicle_state
```

Include `position_ned` only when it comes from genuine CoSys state at that transition.

## Detection and simulator-truth boundary

Pratik's mission/vehicle/coverage/movement producer must not emit Unreal survivor actor
truth as a live detection. Hidden simulator truth remains post-run evaluation input for
Abhijan. If a designated detector actually runs on a camera frame, its output may use the
existing `observation` event contract under `<node>.perception`; it must include real model
output confidence, normalized bounding box, frame ID, modality and model identity/hash.
That producer assignment must remain coordinated with Suyash and Samik.

## Complete executable sample

Use this checked-in sample as the format reference:

```text
codebase/examples/pratik_direct_ethernet_event_sample.jsonl
```

It contains all five initial assignments, all five vehicle/link/coverage streams, genuine-
field NED examples, the obstacle -> HOLD -> deflection -> route-rejoin transition sequence,
and mission completion. The numeric values are examples only; never replay them as live
measurements.

Validate any generated JSONL before wiring it into the controller:

```powershell
Set-Location .\codebase
py -3 -m tools.rescue_event_collector ingest `
  --mission-id OP-VARUNA-001 `
  --input examples\pratik_direct_ethernet_event_sample.jsonl `
  --log results\pratik-format-check.jsonl
```

Expected result begins with `PASS accepted=`. This format check is not a live CoSys claim.

## Top-view feed

Events and imagery remain separate. If Pratik provides a top-view image/MJPEG endpoint,
send Abhijan the exact HTTP URL plus its update rate and resolution. The dashboard can use
it as `VITE_PRATIK_TOP_VIEW_URL`. Precise image-to-NED overlay requires a calibrated
pixel/NED transform; until supplied, the retained NED map remains authoritative.

## Acceptance before demo claim

1. Abhijan's simulation-only launcher reports that `.14:8771` is source-locked to
   Pratik's detected `.11` address.
2. Pratik sender health passes against Abhijan `.14:8771`.
3. A real `mission_started` reaches the Mac collector.
4. All five cards show only real `vehicle_state`/`link_state` values.
5. All five genuine positions appear on the NED map.
6. Exact coverage cells change color and survive collector restart/replay.
7. Obstacle, HOLD, deflection and rejoin appear in order from retained events.
8. Missing coordinates remain absent; the dashboard never invents them.
9. Model-hash authorization remains a separate Jetson/Alpha data plane.
