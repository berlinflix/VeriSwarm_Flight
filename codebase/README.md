# VeriSwarm

Hardware-attested distributed inference for autonomous UAV swarms. After each on-board inference cycle a drone emits a cryptographically signed **receipt** binding its identity, the input it observed, the model it ran, and the action it chose. Peers verify the signature, the model provenance, and — when their camera footprints overlap — the action itself, and a Byzantine-fault-tolerant vote accepts or rejects the decision in real time, with no ground link.

Two named methods:
- **DIA-BCV** — Distributed Inference Attestation with Byzantine Cross-Verification (detection).
- **CTI-SF** — Consensus-Triggered Isolation with Safe-Fallback (prevention).

Alpha, the originator, is hardware-rooted: its Ed25519 signing key lives in an OP-TEE secure element on an NVIDIA Jetson Orin Nano and never leaves it. The peer nodes sign in software.

## Layout
- `protocol/` — receipts, signing/verification (`receipts.py`); reputation-weighted PBFT consensus, co-visibility-gated semantic check (`peer_consensus.py`); footprint geometry (`geometry.py`); protobuf bridge.
- `signing/` — OP-TEE hardware signing backend (`optee_backend.py`).
- `optee/` — the OP-TEE Trusted Application (`ta/`) and host Client Application (`host/`).
- `node/` — live distributed runtime: gRPC `AttestationService` server, originator client, and the 3/5/7-node orchestrators (software and hardware-rooted).
- `perception/` — YOLOv8 detections → action vectors, model hashing, adversarial-patch tooling.
- `sim/` — PX4 SITL + Gazebo flight: pose probe, multi-drone co-visibility from flown poses, frame capture, YOLO on frames.
- `eval/` — experiment runner (`run_all.py`), figure generation (`make_figures.py`), unattested baseline.
- `tests/` — property-based tests for every module.
- `proto/` — gRPC / protocol-buffer service definition.

## Running
```bash
python3 -m pytest -q                 # tests

python3 -m eval.run_all              # software experiments -> results/*.csv
python3 -m eval.make_figures         # figures/ from the CSVs

python3 -m node.run_distributed      # live distributed protocol over gRPC (3/5/7 nodes)
```

### Hardware (Alpha node — Jetson + OP-TEE)
Build/flash the Trusted Application and Client Application (see `optee/`), then:
```bash
python3 -m eval.run_all --hardware                                          # OP-TEE signing latency + energy
VERISWARM_OPTEE_CA=$PWD/optee/host/veriswarm_optee_ca python3 -m node.run_phaseb   # hardware-rooted distributed consensus
```

### Simulation (PX4 SITL + Gazebo)
Launch a PX4 multi-vehicle swarm, then `python3 sim/multi_drone.py` to measure co-visibility from real flown poses. See comments in `sim/`.

## Dependencies
Python 3.10, PyNaCl, grpcio, ultralytics (YOLOv8), pymavlink, matplotlib. The `yolov8n.pt` weights are fetched automatically by ultralytics on first use (kept out of git).

## Citation
Accompanies a paper in preparation — S. Singh, SCOPE, VIT Chennai. Venue/DOI to be added on acceptance.
