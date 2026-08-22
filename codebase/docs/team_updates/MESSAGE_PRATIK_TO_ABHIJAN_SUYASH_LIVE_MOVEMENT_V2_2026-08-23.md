# Pratik live movement-v2 handoff

- owner: Pratik
- branch: `pratik/sih26177-disaster-cosys`
- implementation commit: `b32f562`
- consumed Abhijan commit: `feb7101bece902a2b1cd78f0c4fa1e986c8975e7`

## What works

The separate `run_factorycity_movement_v2.py` connects measured CoSim depth, collision and
vehicle state to `SensorDrivenMovementSupervisor`, exact `CellLedger` state,
restart-safe `DurableMovementEvents`, and `GatedCommandDispatcher`. Actual API-control,
arm, takeoff, velocity/deflection/rejoin, HOLD hover, QUARANTINE hover-land and rooftop
descent boundaries are gated. The accepted nominal-v1 runner is unchanged.

Each exact producer has its own durable SQLite outbox. Producer sequences survive process
restart, so delivered rows cannot cause `event_id` reuse. Abhijan's Windows-to-Mac sender
can drain these files directly.

## Run and test

```powershell
powershell -ExecutionPolicy Bypass -File .\ops\start_pratik_movement_v2.ps1
```

```powershell
..\.venv-cosys341\Scripts\python.exe -m pytest `
  codebase\tests\test_factorycity_movement_v2_live.py `
  codebase\tests\test_factorycity_movement_v2.py `
  codebase\tests\test_movement_security.py `
  codebase\tests\test_rescue_cell_movement_extension.py `
  codebase\tests\test_rescue_ethernet_ingress.py `
  codebase\tests\test_factorycity_disaster.py `
  codebase\tests\test_abhijan_cell_dashboard_replay.py `
  codebase\tests\test_rescue_outbox.py -q
```

Result: `82 passed`.

## Interface

The runner reads an atomically replaced `veriswarm.factorycity.authorization_snapshot.v1`
file containing a canonical current authorization event for each of `alpha`, `bravo`,
`charlie`, `delta`, and `echo`. It reloads the file at every command boundary. Missing,
malformed or older-than-two-second authorization produces fail-closed HOLD; it never
generates ALLOW itself.

Events remain:

```text
Pratik Windows 192.168.50.11
  -> Abhijan Mac 192.168.50.14:8771
  -> collector 127.0.0.1:8770
  -> dashboard 127.0.0.1:5175
```

## Limitation / next peer task

The reviewed direct Ethernet commit currently carries rescue events Windows-to-Mac only.
Abhijan must provide a reviewed Mac-to-Windows atomic authorization refresher (all five
leases, continuously newer than two seconds). Until then, live movement-v2 correctly
stops at preflight or enters HOLD; no live movement-v2 PASS is claimed.
