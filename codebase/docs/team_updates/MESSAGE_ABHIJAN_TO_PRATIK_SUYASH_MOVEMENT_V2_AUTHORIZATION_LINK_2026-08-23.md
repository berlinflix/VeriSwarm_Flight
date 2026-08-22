# Abhijan movement-v2 authorization-link handoff

- owner: Abhijan
- branch: `codex/abhijan-rescue-security`
- implementation commit: `c74fb42`
- consumed Pratik movement-v2 implementation: `b32f562`
- consumed Pratik handoff: `809af65`
- latest Suyash rescue-integration tip reviewed: `d0bd417`

## Outcome

The missing reverse data path is implemented. Abhijan's Mac now maintains one current
movement decision for each simulated vehicle and publishes a complete, fresh five-event
authorization snapshot to Pratik's Windows PC every 500 ms. Pratik's receiver accepts
only Abhijan's exact wired source address, validates the canonical rescue events, rejects
replay/out-of-order snapshots and atomically replaces the file consumed by
`run_factorycity_movement_v2.py`.

This closes the peer task in Pratik's movement-v2 handoff. It does not claim that a live
FactoryCity qualification has passed; that requires the three retained runs below.

## Frozen topology

```text
Pratik Windows 192.168.50.11
  rescue events -> Abhijan Mac 192.168.50.14:8771
  movement leases <- Abhijan Mac 192.168.50.14 -> 192.168.50.11:8772

Abhijan Mac loopback only
  collector 127.0.0.1:8770
  movement policy/control 127.0.0.1:8773
  dashboard 127.0.0.1:5175
```

There is no bearer token on this direct transport. Source-IP restriction is acceptable
only on the physically isolated demo Ethernet. It is not cryptographic producer
authentication and must not be exposed through a router, Wi-Fi or public network.

## Files

```text
codebase/tools/movement_authorization_link.py
codebase/tests/test_movement_authorization_link.py
ops/start_pratik_authorization_receiver.ps1
ops/start_abhijan_movement_v2_mac.sh
ops/set_pratik_movement_authorization.sh
ops/start_abhijan_pratik_mac.sh
codebase/c2_dashboard/vite.config.js
codebase/c2_dashboard/src/App.jsx
codebase/c2_dashboard/src/styles.css
```

## What Pratik must take

Pratik needs only the receiver implementation and launcher from Abhijan's branch. From
Pratik's clean movement-v2 worktree:

```powershell
git fetch origin
git restore --source origin/codex/abhijan-rescue-security -- `
  codebase/tools/movement_authorization_link.py `
  ops/start_pratik_authorization_receiver.ps1
git add codebase/tools/movement_authorization_link.py ops/start_pratik_authorization_receiver.ps1
git commit -m "feat(pratik): receive Abhijan movement-v2 leases"
```

Do not take Abhijan's dashboard files into the simulator branch. Abhijan owns and runs
the dashboard on the Mac.

## Exact startup order

1. Pratik, Windows terminal A, starts the atomic receiver and leaves it open:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\ops\start_pratik_authorization_receiver.ps1
   ```

2. Abhijan, Mac terminal A, starts the direct rescue path, five-lease publisher and
   dashboard:

   ```bash
   cd /Volumes/HyperDrive/Development/VERISWARM_SIH_RESCUE_SECURITY
   ./ops/start_abhijan_movement_v2_mac.sh
   ```

   First startup initializes all five vehicles to `HOLD`. The launcher refuses the wrong
   IPs, Wi-Fi routing, an unavailable Windows receiver or occupied local ports.

3. Abhijan opens `http://127.0.0.1:5175`, scrolls to **Coverage Heatmap & Movement
   Safety**, and verifies `LEASE LINK LIVE`. No movement should be released while the
   panel says `FAIL-CLOSED HOLD` or `LINK NOT ENABLED`.

4. Pratik, Windows terminal B, starts the existing durable rescue sender:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\ops\start_pratik_rescue_sender.ps1
   ```

5. Pratik starts Unreal/CoSim and, in Windows terminal C, runs movement-v2:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\ops\start_pratik_movement_v2.ps1
   ```

6. After all operators confirm the scene is safe, Abhijan selects **ALLOW ALL** in the
   dashboard. The CLI equivalent is:

   ```bash
   ./ops/set_pratik_movement_authorization.sh ALLOW reviewer_nominal_release all
   ```

## Retained qualifications still required

1. **Nominal:** five fresh `ALLOW` leases; retain A-to-B movement, deflection/rejoin,
   coverage and mission events.
2. **Transient HOLD:** set one vehicle to `HOLD`; verify it cancels new route motion and
   hovers while the other allowed vehicles continue. Return that vehicle to `ALLOW` and
   verify resumption only if the current Pratik runner supports the non-terminal hold
   path.
3. **Quarantine:** set one vehicle to `QUARANTINE`; verify hover-land/terminal
   unavailability and reassignment of unfinished cells. Do not attempt to resume a
   quarantined vehicle in the same run; reset the simulator for a new run.

The dashboard controls desired simulation authorization. Pratik's movement-v2 runner is
the only component allowed to issue CoSim movement, hover or landing commands.

## Scope separation

- These five dashboard controls govern only Pratik's simulated vehicles.
- Jetson Alpha model-hash qualification remains a separate retained-evidence path.
- A simulation `QUARANTINE` click is not proof of a model-hash attack.
- Person/hazard observations remain genuine detection events and are never generated by
  the authorization panel.
- Hidden simulator truth remains evaluation-only and must not enter movement or
  authorization decisions.

## Validation completed by Abhijan

- movement authorization link: `9 passed`;
- focused authorization/movement/ingress: `23 passed`;
- dashboard regressions: `13 passed`;
- Vite production build: passed, `1983` modules transformed.

## Live blocker

`NONE` for Abhijan-side code. Pratik must take the two receiver files, then both sides
must retain the three live FactoryCity runs. Until that happens, report the link as
implemented and tested, not live-qualified.
