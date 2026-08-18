# Abhijan — Internal Hackathon Plan for 19 August 2026

**Scope:** temporary qualification plan. Keep `ABHIJAN_EXECUTION_PLAN.md` unchanged as the full Internal Hackathon + SIH execution plan.

**Five-cable update:** read `FIVE_CABLE_EXECUTION_FREEZE_2026-08-19.md` first. Keep
Abhijan's own Mac wired at `192.168.50.14` as the attack-control/evidence/projector host.
Samik operates the camera application on P2; Ayush only designs it. Abhijan retains his
independent terminal and receives no Bravo private material.

## Your outcome tomorrow

Deliver one controlled physical perception attack and one deterministic protocol attack that are reproducible, measurable and clearly separated from the verifier's verdict. Own the audience-facing attack choreography and evidence view.

## Priority order tonight

### Your Codex lane

Use Codex to produce attack manifests, expected-versus-actual validation, evidence indexing and projector-ready summaries. Keep the injector and oracle separate: generated attack code may select the frozen test input but may not manufacture the verifier result. Review artifact hashes, paths and expected reason codes yourself, and test against the integrated runner instead of a duplicate local verdict implementation.

### A-T1 — Freeze the physical attack card

Choose one primary printed adversarial/occlusion artifact and one plain clean control. Do not bring a collection of uncharacterized attacks onto the stage.

Record:

- artifact file hash and print settings;
- target object/class and approved detector model hash;
- camera receiving the artifact;
- camera distance, angle, lighting and placement marks;
- clean baseline observations;
- expected change in measured class/presence/occupancy or co-visibility evidence;
- expected system outcome: dispute or abstention, never a hard-coded “attack detected” claim;
- removal/recovery step;
- three rehearsal results and their raw evidence paths.

The attack passes only if the clean control is stable and the attack effect is reproducible. If the effect depends on a tiny unrepeatable angle, replace it with a more reliable controlled occlusion/semantic mismatch and label it accurately.

### A-T2 — Freeze one deterministic protocol attack

Use the qualification runner's unapproved/tampered model-hash case as the primary protocol attack. Its expected outcome must be an explicit peer rejection and HOLD at the decision layer.

Prepare one offline secondary case—replay or exact-receipt mutation—for the evidence bundle. Do not perform arbitrary live packet editing, dependency replacement or last-minute model downloads.

Your injector may choose the test input but must not write the expected verdict into the result. The signed receipt, verifier and peer reason codes must determine the outcome.

### A-T3 — Build the projection view and evidence index

On Abhijan's Mac at `192.168.50.14`, prepare a simple full-screen view that can show:

- Camera A and Camera B clean/attacked frames.
- Measured claim difference and explicit reason code.
- Alpha's OP-TEE fingerprint and preflight PASS.
- Alpha/Bravo/Charlie Ethernet readiness.
- Clean ACCEPT and attacked HOLD with expected versus actual outcomes.
- CoSys flight view and final collision/landed result.

Do not depend on the planned event collector or console if they are not implemented. A frozen terminal layout plus an HTML/Markdown result page is acceptable. All results must come from saved program output, not manually typed green/red labels.

### A-T4 — Prepare the 45-second attack narration

Use this sequence:

1. “Both cameras first see the clean control.”
2. “I am changing only Camera A's physical input using this recorded artifact.”
3. “The attack changes measurable sensor evidence; it cannot set the verifier's verdict.”
4. Point to the exact measured difference and reason code.
5. Remove the artifact and show recovery.

For the protocol case: “This receipt claims an unapproved model hash. The peer independently compares the signed claim with policy and rejects it.”

## Tomorrow startup checklist

- Bring two copies of the primary artifact, the clean control, tape/stand and printed placement card.
- Verify lighting and camera positions before the final rehearsal, then mark them.
- Connect L2/Mac by Ethernet; verify `.14`, projector resolution and clock offset.
- Copy the frozen evidence bundle locally and verify its hashes.
- Confirm the live display is readable from the back of the room.
- Rehearse placing and removing the artifact without touching a camera/cable.
- Confirm that no attack tool contains private keys, approval secrets or a preselected result.

## Your on-stage actions

- Operate the projected evidence view.
- Place the physical artifact only when Suyash cues you; remove it on cue.
- State what input you changed and what measured field changed.
- Trigger only the frozen model-hash attack case during the LAN section.
- Point to the peer's reason code and HOLD; do not say the simulator drone was automatically held unless that adapter is truly connected and tested.
- Switch cleanly to Pratik's flight view and show the final evidence summary.

## Pass/fail and handoff

GO only if the physical clean and attack conditions each reproduce three times, the model-hash case rejects twice, and the projection view shows real program outputs. Call NO-GO if clean evidence is already unstable, camera failure is mistaken for an attack, or the injector can choose the verdict.

If the physical attack fails live, do not improvise. Show the labelled unedited rehearsal recording and its matching raw evidence.

## Evidence you own

- Physical attack card and artifact SHA-256.
- Clean/attack raw and annotated frames.
- Expected-versus-actual oracle record for both attack cases.
- Projector layout and read-only backup evidence.
- Backup rehearsal recording.

## Do not claim

Do not claim the patch defeats every detector, that an abstention proves an adversarial attack, or that tomorrow's attack is already controlling the CoSys vehicle. The value is the real, independently verifiable evidence chain.

## If the base demo is finished early

Use Pratik's already captured Alpha/Bravo/Charlie frames for a separate three-view detector replay. Hash each frame, run the same pinned model/config, and show the three measured claims without manually editing them. If the views do not satisfy the clean agreement contract, retain them as honest disagreement evidence; do not tune or relabel them to manufacture quorum. This extension may accompany the three-drone transport view but remains separate from actuator control.
