# VeriSwarm

Security research and simulation for signed, cross-checked UAV decisions. After
each inference cycle a drone emits a receipt binding the mission/epoch, identity,
sequence, canonical input, model, runtime measurement, pose estimate, action
frame, action and validity interval. Peers verify the signature and allowlists,
then compare co-visible observations. A separate fail-closed supervisor decides
whether the resulting evidence is sufficient to release a motion request.

The OP-TEE backend protects the Ed25519 key and signs receipt bytes. It does
**not yet attest that inference itself executed inside trusted code**. A signed
pose is also an authenticated claim, not proof that a compromised host reported
its location honestly. The current signed quorum/tally is an experimental protocol with equivocation
detection, not a formally proven Byzantine agreement implementation. Use the
project in simulation/SITL; do not connect it to flight actuators without the
production acceptance work in `SIMULATION_AND_FLIGHT_GATES.md`.

Two named methods:
- **DIA-BCV** — Distributed Inference Receipts with cross-verification (detection research).
- **CTI-SF** — Consensus-Triggered Isolation with Safe-Fallback (prevention).

The Alpha experiment can protect its Ed25519 signing key in OP-TEE secure
storage on an NVIDIA Jetson Orin Nano; peer experiments currently sign in
software. This roots the key operation in the TEE, not the inference pipeline.

## Layout
- `protocol/` — receipts, signing/verification (`receipts.py`); reputation-aware quorum tally, co-visibility-gated semantic check (`peer_consensus.py`); footprint geometry (`geometry.py`); protobuf bridge.
- `signing/` — OP-TEE hardware signing backend (`optee_backend.py`).
- `optee/` — the OP-TEE Trusted Application (`ta/`) and host Client Application (`host/`).
- `node/` — bounded gRPC runtime, mission loop, event chain, originator and orchestrators.
- `perception/` — atomic YOLOv8 action + class-aware claim results, model hashing,
  safety supervision, and adversarial-patch tooling.
- `sim/` — PX4 SITL + Gazebo flight: pose probe, multi-drone co-visibility from flown poses, frame capture, YOLO on frames.
- `eval/` — experiment runner (`run_all.py`), figure generation (`make_figures.py`), unattested baseline.
- `tests/` — deterministic unit, integration, adversarial and regression tests.
- `proto/` — gRPC / protocol-buffer service definition.

## Running
```bash
python3 -m pytest -q                 # complete regression/adversarial suite
python3 -m sim.closed_loop           # deterministic end-to-end safety oracle

python3 -m eval.run_all              # software experiments -> results/*.csv
python3 -m eval.make_figures         # figures/ from the CSVs

python3 -m node.run_distributed      # live distributed protocol over gRPC (3/5/7 nodes)
```

### Hardware-key experiment (Alpha node — Jetson + OP-TEE)
Build/flash the Trusted Application and Client Application (see `optee/`), then:
```bash
python3 -m eval.run_all --hardware                                          # OP-TEE signing latency + energy
VERISWARM_OPTEE_CA=$PWD/optee/host/veriswarm_optee_ca python3 -m node.run_phaseb   # TEE-key distributed experiment
```

### Simulation (PX4 SITL + Gazebo)

Start with `python3 -m sim.closed_loop`; it requires no simulator and asserts
the expected outcomes for honest traffic, model swap, semantic attack, packet
loss and equivocation. Launch a PX4 multi-vehicle swarm only after that passes,
then use `python3 sim/multi_drone.py` for the measured-pose layer. The script now
refuses to arm on an unhealthy position estimate and commands LAND when its
measurement completes.

## Dependencies

Python 3.10+. Install `requirements-core.txt` for protocol/tests or
`requirements-sim.txt` for the full simulation toolchain. Model weights must be
fetched and hashed before an offline run; automatic first-use downloads are not
a reproducible mission procedure.

## Citation
Accompanies a paper in preparation — S. Singh, SCOPE, VIT Chennai. Venue/DOI to be added on acceptance.
