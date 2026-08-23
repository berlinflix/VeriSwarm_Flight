# VeriSwarm Mac fallback demo

## Why this exists

Use this only when Pratik's Windows/Unreal/CoSys host is unavailable. It runs a
deterministic five-drone **data-plane simulation** on Abhijan's Mac and feeds the
existing rescue collector and dashboard through the frozen event schema.

It demonstrates dashboard integration, mission state, five moving NED tracks,
coverage, a flood marker, a synthetic person candidate, movement safety events,
HOLD/QUARANTINE policy outcomes, reassignment and retained reporting.

It does **not** demonstrate Unreal rendering, CoSys physics, AirSim RPC, real camera
inference, physical obstacle avoidance or measured collision clearance. The dashboard
therefore displays `MAC FALLBACK · SYNTHETIC CONTRACT-DRIVEN EVENTS`.

The Jetson model-hash qualification remains a separate lane and is not started or
modified by these commands.

## Start from zero

Close any earlier dashboard/collector terminal with `Ctrl+C`. Then open Terminal A:

```bash
cd /Volumes/HyperDrive/Development/VERISWARM_SIH_RESCUE_SECURITY
./ops/start_mac_fallback_dashboard.sh
```

Open the printed URL:

```text
http://127.0.0.1:5175
```

Keep Terminal A open. In Terminal B run one of the following.

### Scenario 1: nominal rescue plus obstacle deflection

```bash
cd /Volumes/HyperDrive/Development/VERISWARM_SIH_RESCUE_SECURITY
./ops/run_mac_fallback_mission.sh nominal
```

Expected sequence:

1. five vehicles become READY and ONLINE;
2. all five take off into SEARCHING;
3. coverage cells fill along the frozen A-to-B NED route;
4. the synthetic flood hazard appears;
5. Alpha detects an obstacle, enters a safety HOLD, selects a deflection and rejoins;
6. a clearly labeled synthetic person candidate appears;
7. all vehicles land and the mission reaches PASS with 10/10 cells.

### Scenario 2: temporary operator HOLD

Restart Terminal A first so the collector gets a new evidence log, then run:

```bash
./ops/run_mac_fallback_mission.sh hold
```

Bravo pauses while the other vehicles continue. The operator then returns Bravo to
ALLOW and it catches up before landing.

### Scenario 3: QUARANTINE and task reassignment

Restart Terminal A first, then run:

```bash
./ops/run_mac_fallback_mission.sh quarantine
```

Alpha is quarantined for a simulated integrity-policy failure. `route_cell_05` is
reassigned to Bravo, the four healthy vehicles continue, and the mission completes.

## Evidence and reset rule

Every start creates a new timestamped append-only file under:

```text
codebase/results/mac_fallback_rescue_events.<UTC timestamp>.jsonl
```

Do not run a second scenario against the same collector. Each simulated producer
starts at source sequence 1, so restart Terminal A before changing scenarios. This
preserves the earlier evidence and gives the next run a fresh log.

## Reviewer wording

Say:

> Our Windows physics host became unavailable, so this is the contract-driven recovery
> path. It exercises the same frozen mission/event interfaces and command-centre logic
> on macOS. The dashboard explicitly labels all generated movement, hazard and person
> data as synthetic. Our Jetson model-integrity evidence is separate and remains real.

Do not call this run live Unreal, live CoSys, physical flight, or measured obstacle
avoidance evidence.
