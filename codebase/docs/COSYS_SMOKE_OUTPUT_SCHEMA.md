# CoSys smoke and preflight output contracts

## Flight evidence: `veriswarm.airsim_smoke.v2`

`process_result` is a closed enum:

- `PASS`: every flight, landing, cleanup and reset invariant passed and the
  landed-state deviation was not applied.
- `PASS_WITH_APPROVED_SIMULATOR_DEVIATION`: every invariant passed and the
  runner applied the frozen `QB-LANDED-STATE-001` landed-state deviation.
- `FAIL`: any configuration, startup, RPC, flight, landing, cleanup, reset or
  deviation-authorization requirement failed.

Both successful values require `pass: true` and produce process exit code `0`.
`FAIL` requires `pass: false` and produces a nonzero process exit code.

The deviation result is valid only when the recorded values are exactly:

```json
{
  "id": "QB-LANDED-STATE-001",
  "approved": true,
  "approved_by": "Suyash",
  "approval_reference": "SUYASH_QB_LANDED_STATE_001_DISPOSITION_2026-08-19.txt sha256:8836ACEAD75A64FF21EA5D1E0DB21B0402ACA0BDC88FDDAF4C3135C9A514EA0B",
  "applied": true
}
```

The frozen configuration SHA-256 binds this approval reference to the rest of
the run input. It identifies Suyash's independently frozen
`SUYASH_QB_LANDED_STATE_001_DISPOSITION_2026-08-19.txt` artifact (2,488 bytes,
SHA-256 `8836ACEAD75A64FF21EA5D1E0DB21B0402ACA0BDC88FDDAF4C3135C9A514EA0B`).
The runner never invents or reconstructs that private artifact.

`rpc_execution` records the executor model, whether a fresh fail-safe context
was created after a timeout, and every timeout observed. A timed-out live RPC
process is terminated and cannot later contribute a successful result.

The same abort rule applies when a phase safety check fails while an async
command is still joining: the command-owner process is terminated, its local
join thread is awaited, and the original collision/safety error is retained.
Before abort cleanup starts, the runner creates fresh command and observation
contexts; cleanup never waits behind the terminated command's RPC lock.

Configuration and startup refusals use `process_result: "FAIL"`; their more
specific reason is retained in `failure_class`.

## Read-only evidence: `veriswarm.cosys_preflight.v1`

Run from `codebase` with a new create-once output name:

```powershell
python -m sim.cosys_smoke_flight --preflight-only --config PATH_TO_FROZEN_QB_CONFIG.json --out QB-RPC-PREFLIGHT-NEW-ID.json
```

This mode refuses any endpoint except `127.0.0.1:41451`, any runtime except
Q-B, or any vehicle name except raw `Drone1` **before constructing a client or
starting a network/process operation**. It performs only:

1. client construction;
2. `ping()`;
3. `listVehicles()`;
4. `getMultirotorState(vehicle_name="Drone1")`;
5. client shutdown.

It never enables API control, arms, takes off, resets or moves the vehicle.
The JSON passes only when the read-only checks and clean client shutdown all
succeed. A poisoned or force-terminated RPC context reports
`client_shutdown: false`; it is never represented as a clean shutdown. Console
capture remains an operator-owned, create-once companion file.
