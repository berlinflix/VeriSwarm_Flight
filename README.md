# VeriSwarm — SIH 2026

Hardware-attested distributed inference for adversarially-robust UAV autonomy, built into a
live, judge-attackable demo for Smart India Hackathon 2026.

## 📌 Read in this order

| # | File | What it is |
|---|---|---|
| 1 | **[urgent_new_changes.md](urgent_new_changes.md)** | **Read first, every time.** Overrides everything below it. Newest decisions are at the top. |
| 2 | [SIH_2026_Build_Plan.md](SIH_2026_Build_Plan.md) | Architecture, the seven attacks, member plans, tier gates, demo run-of-show. |
| 3 | [HARDWARE_LIST.md](HARDWARE_LIST.md) | What to buy, what not to, and the gotchas that cost a day each. |

If `urgent_new_changes.md` and the build plan disagree, **the urgent file wins.**

## Who does what

| | Who | Platform | Owns |
|---|---|---|---|
| M1 | Suyash | WSL + Jetson | Protocol integration, mission loop, event bus, attack surface, OP-TEE |
| M2 | Abhijan | Mac | Console (Streamlit) + the Python half of the simulator |
| M3 | Pratik | Windows | Unreal/AirSim, scene, patch plane → then demo operations |
| M4 | *unassigned* | any | Problem statement, deck, pitch, judge Q&A |

## Repo layout

```
urgent_new_changes.md    Override log — read first
SIH_2026_Build_Plan.md   The plan
HARDWARE_LIST.md         Shopping list + gotchas
codebase/                The VeriSwarm implementation
  protocol/              Receipts, Ed25519 signing, peer verification, BFT tally, reputation
  node/                  gRPC runtime — client, server, manifest, distributed runners
  perception/            YOLOv8n → action vector; model hashing; patch application
  signing/               OP-TEE secure-element signer (Jetson)
  optee/                 Trusted Application (C) + host client
  sim/                   PX4/Gazebo flight, co-visibility measurement
  eval/                  Experiment harness, figures, adversarial-patch transfer study
  tests/                 102 property tests
  results/               Measured CSVs behind the paper's tables
```

## Setup

The Python environment (PyTorch, ultralytics, grpcio, pynacl) lives in WSL on M1's machine.
Others need Python 3.10+, then:

```bash
pip install ultralytics grpcio grpcio-tools pynacl opencv-python streamlit
```

Model weights are **not** in the repo and don't need to be — both are generated
automatically. Ultralytics downloads `yolov8n.pt` on first use, and `ensure_tampered()` in
`codebase/eval/perception_scenarios.py` builds the tampered copy.

Verify your checkout is sound before changing anything:

```bash
cd codebase && python -m pytest
```

All 102 tests should pass. That's your baseline.

## Relationship to the research paper

This is a fork of the implementation behind *VeriSwarm: Hardware-Attested Distributed
Inference for Adversarially-Robust UAV Autonomy* (Singh & Subbulakshmi, VIT Chennai).

The paper's repo — [berlinflix/veriswarm](https://github.com/berlinflix/veriswarm) — is
**frozen** so every number in the paper stays reproducible. All SIH development happens
here, and the two diverge deliberately. Never push SIH changes to the paper repo.
