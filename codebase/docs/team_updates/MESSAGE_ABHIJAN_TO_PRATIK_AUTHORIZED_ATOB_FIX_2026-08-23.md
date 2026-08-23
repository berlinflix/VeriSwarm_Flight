# Abhijan to Pratik — movement-first authorized A-to-B fix

Branch: `codex/pratik-authorized-atob`

## Observed failure

The direct Ethernet and dashboard paths were live, but movement-v2 ended with
`movement_v2_route_runtime_exceeded`. All five vehicles remained at Point A in `HOLD`.
The sensor route loop synchronously captured five DepthPlanar frames before every short
movement step. On the live Windows/Unreal system that work exceeded the frozen control
period, so the supervisor correctly held each vehicle and horizontal progress never
started.

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
"route_mode": "authorized-nominal"
```

## Validation

- focused movement and dashboard integration suite: `87 passed`
- full repository suite: `569 passed`

