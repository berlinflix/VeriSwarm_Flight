"""
VeriSwarm live distributed runtime (Stage 3).

This package turns the in-process protocol (`protocol/`) into a real distributed
system: each peer drone runs an `AttestationService` gRPC server (`server.py`),
and an originator (`client.py`) signs a receipt, broadcasts it to its peers over
the network, collects their signed votes, and tallies a Byzantine-consensus
decision. `run_distributed.py` orchestrates 3/5/7-node sessions and measures the
real end-to-end consensus latency and decisions.

Nothing here re-implements the protocol: signing, verification, the co-visibility
gate, and the consensus tally are the exact `protocol/` classes, exercised over
gRPC instead of in one process.
"""
