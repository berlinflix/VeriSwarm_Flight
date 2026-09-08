# VeriSwarm 

Hardware-attested distributed inference for adversarially-robust UAV autonomy, built into a
live, judge-attackable demo.



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
  tests/                 286 property + security tests
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

All 286 tests should pass. That's your baseline.

## Relationship to the research paper

This is a fork of the implementation behind *VeriSwarm: Hardware-Attested Distributed
Inference for Adversarially-Robust UAV Autonomy* (Singh & Subbulakshmi, VIT Chennai).

The paper's repo — [berlinflix/veriswarm](https://github.com/berlinflix/veriswarm) — is
**frozen** so every number in the paper stays reproducible. 
