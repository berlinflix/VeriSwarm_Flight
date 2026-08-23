# Abhijan to Pratik — movement-first authorized A-to-B fix

Branch: `codex/pratik-authorized-atob`

## Observed failure

The direct Ethernet and dashboard paths were live, but movement-v2 ended with
`movement_v2_route_runtime_exceeded`. All five vehicles remained at Point A in `HOLD`.
The sensor route loop synchronously captured five DepthPlanar frames before every short
movement step. On the live Windows/Unreal system that work exceeded the frozen control
period, so the supervisor correctly held each vehicle and horizontal progress never
started.

The first `authorized-nominal` hardware run then reached 213 fast control loops but still
timed out with every vehicle in `HOLD`. That isolated a second fault. After the water rose,
the vehicles were near local `z=-8.2`; the route requested `z=-10.0` in one 0.25-second
command. The resulting declared vertical speed was about 7.2 m/s, above the frozen 2.0
m/s limit, so the movement gate correctly replaced every horizontal command with hover.

## Added movement-first route

`authorized-nominal` explicitly uses the already accepted straight Point A-to-Point B
route while retaining:

- one fresh authorization lease per vehicle at every mutating command boundary;
- collision detection and terminal collision handling;
- five-vehicle separation and geofence gates;
- live vehicle-state, assignment, coverage and mission events;
- durable per-producer SQLite outboxes and Abhijan's direct event sender;
- safe airborne hover with retained API control on failure.

It removes DepthPlanar capture/deflection from the critical route loop. Therefore this
run demonstrates authorized A-to-B movement and live dashboard integration, not obstacle
deflection. The existing `sensor` mode remains unchanged for separate optimization and
qualification.

The controller now also:

- performs a separately authorized transition from flood-clearance altitude to cruise
  altitude before starting the A-to-B route;
- slew-limits any residual Z correction to the frozen 2.0 m/s vertical-speed limit;
- retains the latest decision/reason and released-command count for every vehicle in
  `movement_v2_run.json`;
- includes the exact per-vehicle gate reason in any future route-timeout failure.

## Run

After the receiver, Mac integration, event sender and Unreal Play mode are ready, Abhijan
selects `ALLOW ALL`. Pratik then runs:

```powershell
powershell -ExecutionPolicy Bypass -File `
  .\ops\start_pratik_movement_v2.ps1 `
  -RouteMode authorized-nominal
```

Expected console line:

```text
INFO  route mode: authorized-nominal
```

Expected result JSON includes:

```json
"route_mode": "authorized-nominal",
"released_route_commands": {
  "alpha": 1
}
```

Every vehicle's released-command count must be greater than zero. If the run fails, send
the complete `failure`, `last_gate_decisions`, `released_route_commands`, `loop_count` and
`states` fields; do not diagnose from the final `HOLD` state alone.

## Validation

- focused movement/security suite: `43 passed`
- full repository suite: `571 passed`
