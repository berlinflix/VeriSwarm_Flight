# VeriSwarm all-in-one live demo runbook

This run keeps the physical model-hash qualification and Pratik's five-drone
FactoryCity simulation visible in one dashboard. The two evidence planes remain
independent: a Jetson model-hash result never changes a simulated movement lease.

## Frozen Ethernet topology

| Machine | Address | Responsibility |
| --- | --- | --- |
| Jetson Alpha | `192.168.50.10` | OP-TEE model-hash originator and qualifier |
| Pratik Windows | `192.168.50.11` | Unreal/AirSim, movement executor, rescue sender and authorization receiver |
| Bravo | `192.168.50.12` | model-hash verification peer |
| Charlie | `192.168.50.13` | model-hash verification peer |
| Abhijan Mac | `192.168.50.14` | command dashboard, collector, ingress and movement policy |

All five machines must remain on the isolated Ethernet switch. Do not send the
Jetson dashboard token to Pratik. Pratik's rescue events enter the Mac at
`.14:8771`; fresh movement leases go back to Pratik at `.11:8772`.

## Start order

### 1. Pratik Windows terminal A — authorization receiver

Run inside `Documents\VeriSwarm_SIH_Abhijan` and leave it open:

```powershell
Set-Location "$env:USERPROFILE\Documents\VeriSwarm_SIH_Abhijan"
powershell -ExecutionPolicy Bypass -File .\ops\start_pratik_authorization_receiver.ps1
```

Expected: the receiver binds `192.168.50.11:8772`, accepts only the Mac at
`192.168.50.14`, and waits for five fresh leases.

### 2. Jetson Alpha — qualifier

Leave Bravo and Charlie peer services running. On Alpha run and leave open:

```bash
vs peers
vs check
vs dash
```

### 3. Abhijan Mac — one combined launcher

If an older dashboard launcher is running, stop it once with `Ctrl+C`. Then run:

```bash
cd /Volumes/HyperDrive/Development/VERISWARM_SIH_RESCUE_SECURITY
./ops/start_abhijan_rescue_mac.sh
```

This single process starts:

- `127.0.0.1:8765` — SSH tunnel to Alpha's qualifier;
- `127.0.0.1:8770` — canonical rescue collector;
- `192.168.50.14:8771` — Pratik-only rescue ingress;
- `127.0.0.1:8773` — five-lease movement control API and publisher;
- `127.0.0.1:5175` — the dashboard.

It resets all five simulated vehicles to fail-closed `HOLD` every time it starts.
Do not click `ALLOW ALL` yet.

Open `http://127.0.0.1:5175`. Confirm the movement panel says `LEASE LINK LIVE`
and the model-hash panel is ready. `HOLD` at this stage is correct.

### 4. Pratik Windows terminal B — rescue telemetry sender

Run inside the same repository and leave it open:

```powershell
Set-Location "$env:USERPROFILE\Documents\VeriSwarm_SIH_Abhijan"
powershell -ExecutionPolicy Bypass -File .\ops\start_pratik_rescue_sender.ps1
```

Expected: the health check for `http://192.168.50.14:8771/health` passes. The
sender continuously flushes genuine retained CoSim events to the Mac.

### 5. Pratik — Unreal Play mode

Open `FactoryCity_Disaster`, press **Play**, and wait until all five vehicles are
rendered, AirSim is connected, and the vehicles are stable. Do not start the
movement runner before this point.

### 6. Abhijan — release the simulation

In **Five-Lease Safety Control**, click `ALLOW ALL`. Confirm all five cards show
fresh `ALLOW` decisions and the panel still says `LEASE LINK LIVE`.

This releases only the five simulated vehicles. It does not approve a Jetson
model hash and does not weaken the model-hash qualifier.

### 7. Pratik Windows terminal C — A-to-B movement

Only after steps 1–6 pass, run:

```powershell
Set-Location "$env:USERPROFILE\Documents\VeriSwarm_SIH_Abhijan"
powershell -ExecutionPolicy Bypass -File .\ops\start_pratik_movement_v2.ps1 -RouteMode authorized-nominal
```

The movement runner is the only component allowed to command the drones. The
dashboard receives vehicle state, position, coverage, transition and completion
events; it never directly flies Unreal vehicles.

### 8. Model-hash demonstration — independent and concurrent

While the simulation runs, use the **Model Hash** tile:

1. Run **Approved Baseline** and show `2/2 ACK`.
2. Run **Model-Hash Attack** and show `0/2 ACK`, `2/2 DISPUTE`, `HOLD` and
   `model_hash_not_approved`.

The model-hash `HOLD/QUARANTINE` belongs to physical Alpha's qualification result.
It must not stop, release or rewrite Pratik's five simulated movement leases.

## Fail-closed checks

- If the Mac launcher says Pratik's receiver is unavailable, start terminal A
  first and rerun the Mac launcher.
- If the dashboard says `LEASE LINK OFFLINE`, do not run terminal C.
- If any lease is `HOLD` or `QUARANTINE`, the movement runner must refuse that
  vehicle's next gated operation.
- If Pratik telemetry is absent, do not claim live coverage, detections or
  positions. Keep the simulator running and repair terminal B.
- Never start a second Mac launcher; it will collide on ports `5175`, `8770`,
  `8771` or `8773`.

## Shutdown

1. Stop terminal C after the route finishes.
2. Stop Unreal Play mode.
3. Stop terminal B, then terminal A.
4. Press `Ctrl+C` once in the Mac launcher; it closes the dashboard, collector,
   ingress, movement publisher and Jetson tunnel.
5. Stop `vs dash` on Alpha only after the model-hash demonstration is complete.
