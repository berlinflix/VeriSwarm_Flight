# Suyash → Samik Qualification Handoff

**Demo date:** 19 August 2026  
**Owner:** Suyash  
**Receiver:** Samik  
**Status:** OPEN until every `H0` item is complete.  
**Purpose:** freeze exactly what Samik needs for Bravo, protocol validation and the handover to Pratik's CoSys transport proof.

This is a qualification handoff, not the full SIH authority or flight-control handoff. It supplements, and does not replace, the existing execution plans.

For the physical/runtime layout, `FIVE_CABLE_EXECUTION_FREEZE_2026-08-19.md` is
authoritative: Samik P2 runs USB/DroidCam first, releases both sources, then starts Bravo at
`192.168.50.12:51001`; Abhijan retains `.14`; Ayush is camera-code design only.

## 1. Decisions Suyash makes now

- The qualification protocol roster is exactly `alpha`, `bravo`, `charlie`.
- Alpha runs on Jetson and signs locally through OP-TEE. There is no remote signing service.
- Bravo runs on Samik P2 with only Bravo's software simulation key.
- Charlie runs on Suyash L1 with only Charlie's software simulation key.
- The live panel attack is the deterministic unapproved-model-hash case.
- Exact duplicate receipt delivery is treated as idempotent transport, not falsely advertised as a newly rejected attack. A stale newly verified receipt and an expired command are separate offline failure checks.
- Tomorrow's CoSys A-to-B run is a separate transport smoke proof. It is not connected to protocol authorization until the reviewed adapter exists.
- Samik may begin the config-driven smoke client immediately. It must refuse to fly if Pratik's immutable route bundle is absent or incomplete; Samik must not hard-code guessed vehicle names or coordinates.

## 2. Frozen protocol contract

### Topology

| Identity | Machine | Address | gRPC port | Signer |
|---|---|---:|---:|---|
| Alpha | Jetson | `192.168.50.10` | `51000` | OP-TEE; no seed in any file |
| Bravo | Samik P2 | `192.168.50.12` | `51001` | software simulation key; seed only in Bravo-scoped manifest |
| Charlie | Suyash L1 | `192.168.50.13` | `51003` | software simulation key; seed only in Charlie-scoped manifest |

Qualification mission values:

- `protocol_version`: `4`
- `mission_id`: `internal-qualifier-2026-08-19`
- `mission_epoch`: `1`
- `action_frame`: `BODY_FLU_NORMALIZED_VELOCITY`
- receipt/command validity: maximum `2_000_000_000 ns`
- roster: exactly three identities, so Alpha has two voting peers
- clean threshold: `ack_threshold = 2`
- rejection threshold: `dispute_threshold = 1`
- safe behavior for `REJECTED`, `NO_QUORUM`, missing semantic quorum or expired evidence: `HOLD (0.0, 0.0, 0.0)`

Transport is insecure gRPC in `simulation` mode for the isolated internal-demo LAN. Receipt and vote signatures remain verified, but the link is not mTLS. Disable Wi-Fi, restrict the firewall to the wired subnet and do not call this transport production-secure.

### Detector provenance

Hashes measured in Suyash's current workspace on 18 August 2026:

| File | SHA-256 | Use |
|---|---|---|
| `codebase/yolov8n.pt` | `f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36` | only approved live/replay detector for the qualification bundle |
| `codebase/yolov8n_tampered.pt` | `2790b51f22171fb882ebd9352c5068ae00057187c0058ad76c10cef3d748a2a2` | attack input; never add to the allowlist |

These values are provisional until the copied files on Jetson, P2 and L1 are re-hashed. Any mismatch is a failed handoff, not permission to update the manifest.

Expected model-swap peer reason prefix:

```text
model_hash_not_approved:2790b51f22171fb8
```

Expected three-node result: both available peers DISPUTE, `disputes = 2`, protocol outcome `REJECTED`, supervisor/decision display `HOLD`.

### `PerceptionClaim` v4 schema

The exact signed fields are:

```json
{
  "measured": true,
  "detections_present": false,
  "detection_count": 0,
  "class_ids": [],
  "occupancy": 0.0,
  "max_confidence": 0.0,
  "bearing": 0.0
}
```

The values above illustrate a valid measured empty-scene claim; they are not automatically the clean demo fixture. Suyash must generate and freeze the actual clean Alpha/Bravo/Charlie claims from the selected detector-derived frames. All of these rules apply:

- `class_ids` are sorted unique integers in the pinned taxonomy.
- `occupancy`, confidence and bearing are finite and within their protocol ranges.
- `detections_present = false` requires zero count, empty classes and zero detection evidence.
- An unmeasured claim is never treated as an empty scene or semantic agreement.
- Clean panel acceptance requires both Bravo and Charlie to return reason `ok`, `acks = 2`, `semantic_acks = 2`, outcome `ACCEPTED`.

For speed and reproducibility, the LAN qualification may replay three frozen detector-derived frame artifacts—one each for Alpha, Bravo and Charlie. It must be labelled “detector-derived replay over the live LAN,” not live three-camera co-observation. The separate Samik-P2 USB/DroidCam beat is the live physical sensor demonstration.

## 3. Critical implementation correction

Do **not** tell Samik to use the plain command below for the semantic clean case:

```text
python -m node.server --manifest ... --id bravo
```

That CLI supplies no atomic measured `PerceptionSnapshot`. It can verify crypto/provenance, but semantic agreement falls back to `ok_no_observation`, so `semantic_acks` remains zero.

Suyash must provide a narrow `codebase/tools/qualification_peer.py` that:

- loads a node-scoped manifest;
- loads the frozen local frame/result fixture and verifies its hashes;
- exposes a fresh timestamped atomic snapshot containing action, measured claim and pose;
- starts `node.server.serve(..., snapshot_provider=...)`;
- prints READY only after manifest, signer, fixture and listener validation;
- emits vote reason codes without secrets;
- closes the perception worker, gRPC server and channels on Ctrl+C/error.

Samik owns deployment/testing of that reviewed launcher on P2; Suyash owns its shared protocol implementation.

## 4. Bundle Suyash must produce

Use a new immutable bundle ID, for example:

```text
IHQ-20260819-001
```

Required layout:

```text
results/internal_qualifier/IHQ-20260819-001/
  PUBLIC_SHA256SUMS
  public/
    manifest.public.json
    contract.json
    clean-case.json
    model-swap-case.json
    output-schema.json
    network-preflight-template.txt
    go-no-go.md
  alpha-private/
    alpha.manifest.json
  bravo-private/
    bravo.manifest.json
  charlie-private/
    charlie.manifest.json
  fixtures/
    alpha/
    bravo/
    charlie/
  evidence/
    optee/
    protocol/
    network/
```

Distribution rules:

- Samik receives `public/`, `fixtures/bravo/`, and `bravo-private/bravo.manifest.json` only.
- Jetson receives `public/`, `fixtures/alpha/`, and the Alpha-scoped manifest. Alpha has no seed.
- Suyash L1 receives `public/`, `fixtures/charlie/`, and the Charlie-scoped manifest.
- Abhijan receives only `public/` and read-only evidence. He receives no private scoped manifest.
- `manifest.public.json` contains no seeds. Its SHA-256 is the shared roster/config identity.
- Every scoped manifest has its own SHA-256. Do not call the differing scoped files one identical manifest hash.

Suyash should implement `codebase/tools/build_qualification_bundle.py` or equivalent reviewed code using `common.manifest_for_node()`. It must fail if any peer's seed survives in another node's scoped file.

## 5. Exact cases and expected results

### Q-CLEAN

Input:

- approved real model hash;
- Alpha, Bravo and Charlie frozen detector-derived claims agree under the v4 rule;
- all three poses pass the qualification co-visibility configuration;
- Alpha action is the exact action recorded in `clean-case.json`.

Expected:

```text
Bravo vote:   ACK / ok
Charlie vote: ACK / ok
acks: 2
semantic_acks: 2
disputes: 0
outcome: ACCEPTED
decision-layer result: EXECUTE only if the qualification supervisor's remaining health/clearance fixtures are explicitly positive
```

### Q-MODEL-SWAP — the live panel attack

Input: identical case except Alpha receipt uses the tampered weight-file hash, which is absent from the allowlist.

Expected:

```text
Bravo vote:   DISPUTE / model_hash_not_approved:2790b51f22171fb8...
Charlie vote: DISPUTE / model_hash_not_approved:2790b51f22171fb8...
disputes: 2
outcome: REJECTED
decision-layer result: HOLD
```

### Q-PEER-TIMEOUT — offline/failure evidence

With one of two peers unavailable, the remaining single ACK cannot meet the threshold:

```text
acks: 1
semantic_acks: at most 1
missing: 1
outcome: NO_QUORUM
reason: insufficient_votes:ack=1<2,dispute=0<1
decision-layer result: HOLD / consensus_no_quorum
```

### Q-FRESHNESS — offline/failure evidence

- A newly verified receipt outside the freshness window: `stale_receipt`.
- A receipt whose command validity has elapsed at the supervisor: `command_expired`.
- An exact retransmission of an already-seen receipt: same cached vote/idempotent delivery; it must not create a fresh round, extra voter or additional authorization.

Do not present “exact replay was rejected” unless the final runner implements and proves that stronger end-to-end behavior. Deduplication and expiry are the current precise claims.

## 6. Receipt-to-command binding Samik validates

There is no authoritative standalone `command_digest` field in protocol v4. Do not invent one in the UI and imply the verifier authorized it.

Samik validates:

1. `receipt_digest = SHA256(receipt.canonical())`.
2. Every counted vote's `target_receipt_hash` equals that receipt digest.
3. `consensus.target_receipt_hash` equals that receipt digest.
4. The requested command tuple equals `receipt.output` exactly.
5. The released command comes from `SafetySupervisor`; mismatch produces `evidence_command_mismatch` and HOLD.
6. `action_frame`, `valid_for_ns`, receipt timestamp, mission ID/epoch and sequence are displayed/retained.

If a display-only command fingerprint is later added, label it non-authoritative and define its canonical encoding. It cannot replace the exact checks above.

## 7. CLI contract Suyash hands to Samik

These commands become final only after the two MUST BUILD tools exist and `--help` matches them.

Bravo startup on Samik P2:

```powershell
cd C:\path\to\VeriSwarm_SIH\codebase
python -m tools.qualification_peer `
  --manifest C:\path\to\IHQ-20260819-001\bravo-private\bravo.manifest.json `
  --id bravo `
  --fixture C:\path\to\IHQ-20260819-001\fixtures\bravo\snapshot.json `
  --events C:\path\to\IHQ-20260819-001\evidence\protocol\bravo.jsonl `
  --verbose
```

Graceful shutdown: Ctrl+C once, then confirm port `51001` is closed and the process exits successfully. Force termination is a failure to record, not the normal runbook.

Alpha clean case on Jetson:

```bash
cd /path/to/VeriSwarm_SIH/codebase
python -m tools.qualification_protocol_demo \
  --manifest /path/to/IHQ-20260819-001/alpha-private/alpha.manifest.json \
  --case /path/to/IHQ-20260819-001/public/clean-case.json \
  --out /path/to/IHQ-20260819-001/evidence/protocol/clean.json
```

Repeat with `model-swap-case.json` and a new output path. The runner must refuse overwrite and return non-zero when actual and expected results differ.

## 8. Output JSON contract

Minimum fields in every qualification result:

```json
{
  "schema": "veriswarm.internal_qualifier.v1",
  "bundle_id": "IHQ-20260819-001",
  "run_id": "fresh-unique-id",
  "case_id": "Q-CLEAN",
  "created_ns": 0,
  "commit": "40-lowercase-hex",
  "public_manifest_sha256": "64-lowercase-hex",
  "expected": {"outcome": "ACCEPTED", "semantic_acks": 2},
  "actual": {
    "outcome": "ACCEPTED",
    "acks": 2,
    "disputes": 0,
    "missing": 0,
    "semantic_acks": 2,
    "reason": "ack_quorum_reached:2>=2"
  },
  "receipt": {
    "digest": "64-lowercase-hex",
    "model_hash": "64-lowercase-hex",
    "input_hash": "64-lowercase-hex",
    "output": [0.0, 0.0, 0.0],
    "action_frame": "BODY_FLU_NORMALIZED_VELOCITY",
    "mission_id": "internal-qualifier-2026-08-19",
    "mission_epoch": 1,
    "sequence": 1
  },
  "authorization": {
    "allowed": false,
    "released": [0.0, 0.0, 0.0],
    "reason": "explicit-reason"
  },
  "peer_votes": [],
  "pass": true
}
```

`authorization.allowed` in the example is not the clean expected value; the runner fills it from the actual supervisor fixtures. No result may be manually recolored or rewritten for presentation.

## 9. Suyash's execution checklist

### H0 — unblock Samik

- [ ] Receive the actual Alpha OP-TEE public key from the Jetson and pin the same 64-character lowercase value outside the runtime manifest.
- [ ] Re-hash both weight files on every machine receiving them; confirm the approved file matches the frozen hash above.
- [ ] Implement and test `tools/build_qualification_bundle.py` or equivalent node-scoped provisioning path.
- [ ] Generate `manifest.public.json`, Alpha-scoped, Bravo-scoped and Charlie-scoped manifests.
- [ ] Confirm Bravo's file contains Bravo's seed and no other seed; Charlie equivalent; Alpha contains no seed.
- [ ] Record the public manifest hash and every scoped-manifest hash.
- [ ] Freeze one detector-derived frame/result fixture per protocol node and record its claim and hashes.
- [ ] Implement `tools.qualification_peer`; prove it returns `reason=ok` and not `ok_no_observation` for the clean fixture.
- [ ] Implement `tools.qualification_protocol_demo`; prove clean ACCEPT/2 semantic ACKs and model-swap REJECT/HOLD.
- [ ] Freeze the runner CLI and output schema.
- [ ] Run focused tests plus the complete repository suite.
- [ ] Commit the reviewed integrated state and record the new `git rev-parse HEAD`. Current planning baseline `0e873cf42c23ebc2863c69c76bc1617dea661404` is not the freeze commit.
- [ ] Give Samik only his scoped bundle and the reviewed commit; verify his hashes after transfer.

### H1 — required before the full rehearsal

- [ ] On Samik P2, stop `covis_live` gracefully and prove both camera sources are released
      before starting Bravo; run the fresh Jetson OP-TEE preflight separately before Alpha.
- [ ] Run a fresh Jetson OP-TEE preflight with mission ID `internal-qualifier-2026-08-19`, epoch `1`, and a new evidence path.
- [ ] Confirm preflight `actual_pubkey == expected_pubkey` and `signature_verified == true`.
- [ ] Complete the network/clock/firewall sheet for all five wired machines.
- [ ] Receive Pratik's immutable one-drone route bundle and Samik's successful remote `ping/listVehicles` evidence.
- [ ] Run clean, model-swap and peer-timeout checks once over the real LAN.
- [ ] Run the complete three-beat demo twice from cold/reset state.
- [ ] Hash the evidence directory and copy only the public/read-only evidence to Abhijan.
- [ ] Sign the final GO/NO-GO sheet.

## 10. Exact handover cue to CoSys

Use this wording so the panel cannot mistake sequential demonstrations for an already integrated actuator path:

> “The signed protocol slice is complete: the approved case reached two semantic acknowledgements, and the model-swap case was rejected to HOLD. We are now switching to the independent CoSys transport proof. Samik, start the frozen A-to-B smoke flight.”

Do not say “VeriSwarm now commands the drone” tomorrow unless the reviewed CoSys adapter has actually been implemented, fault-tested and included in both cold rehearsals.

## 11. Early-finish extension

Only after all H0/H1 items and two one-drone cold runs pass:

1. Preserve the one-drone bundle as the immutable fallback.
2. Ask Pratik for a separate three-vehicle route bundle using the actual `listVehicles()` names.
3. Samik adds a separate multi-vehicle smoke mode; do not alter the accepted one-drone code path.
4. Require minimum pairwise separation, per-vehicle timeout/collision/land/disarm evidence and one all-vehicle abort command.
5. Run two complete three-drone rehearsals before putting it on stage.

Three scripted vehicles demonstrate multi-drone transport and give the panel a visible swarm foundation. They do not yet prove swarm autonomy, task allocation or protected protocol-gated control.
