# Team message — read and act

Copy and send the text below to the whole team after Suyash shares the pushed branch and
commit.

---

TEAM — FINAL FIVE-CABLE EXECUTION FREEZE

Fetch branch `codex/five-cable-team-plans` and read the exact pushed commit Suyash sends.
Do not pull it into a dirty checkout; use a clean clone/worktree or read it on GitHub.

Everyone reads these first, in order:

1. `urgent_new_changes.md`
2. `FIVE_CABLE_EXECUTION_FREEZE_2026-08-19.md`
3. `INTERNAL_HACKATHON_TOMORROW_COMBINED_PLAN.md`
4. Your individual tomorrow plan
5. Your individual full execution plan

Frozen physical layout — exactly five cables:

- Jetson `192.168.50.10`: Alpha and local OP-TEE signing.
- Pratik `192.168.50.11`: CoSys/AirSim RPC `41451`.
- Samik `192.168.50.12`: USB/DroidCam application first; after release, Bravo `51001`.
- Suyash `192.168.50.13`: Charlie `51003` and orchestration.
- Abhijan `192.168.50.14`: independent attack-control terminal/projector/evidence.
- Ayush has no runtime cable/IP. There is no `.15` endpoint.

Role-specific read/action:

SUYASH

- Read `SUYASH_INTERNAL_HACKATHON_TOMORROW.md`, `SUYASH_EXECUTION_PLAN.md`,
  `SUYASH_TO_SAMIK_QUALIFICATION_HANDOFF.md` and `DEMO_TOPOLOGY.md`.
- Freeze/verify host-MAC-IP mapping, run Charlie, run the fresh Jetson OP-TEE preflight and
  own final GO/NO-GO.
- Accept only Ayush's exact reviewed camera commit plus Samik's real P2 evidence.

SAMIK

- Read `SAMIK_INTERNAL_HACKATHON_TOMORROW.md`, `SAMIK_EXECUTION_PLAN.md`,
  `AYUSH_WEBCAM_EXECUTION_PLAN.md` and `SUYASH_TO_SAMIK_QUALIFICATION_HANDOFF.md`.
- Review Ayush's one frozen camera commit in a clean worktree and reproduce focused tests.
- Connect USB webcam + DroidCam to P2, run the live camera cycles, save evidence, exit and
  prove both sources released; only then start Bravo at `.12:51001`.
- Keep Bravo and its scoped identity on P2. Do not copy it to Ayush.

AYUSH

- Read `AYUSH_WEBCAM_EXECUTION_PLAN.md` and
  `FIVE_CABLE_EXECUTION_FREEZE_2026-08-19.md`.
- Start from `origin/codex/covis-live-ui` commit
  `f0582b6d6b471b023ba6c312fd198978e5ebdd6b` on `ayush/covis-live-final`.
- Design/test the Windows-capable USB+DroidCam application, focused tests and documentation.
- Push one frozen commit and send Samik the exact hash, base, changed-file list, exact test
  output and proposed P2 command. Do not send loose source files or generated/private data.
- Ayush does not operate a wired demo machine.

ABHIJAN

- Read `ABHIJAN_INTERNAL_HACKATHON_TOMORROW.md` and `ABHIJAN_EXECUTION_PLAN.md`.
- Keep your own Mac at `.14`; prepare the attack-control terminal, artifact, placement card,
  expected-outcome oracle, evidence view and projector.
- Apply/remove the artifact only when Samik cues the clean/attack/recovery stages. Never
  manufacture or overwrite the verifier result.

PRATIK

- Read `PRATIK_INTERNAL_HACKATHON_TOMORROW.md` and `PRATIK_EXECUTION_PLAN.md`.
- Keep P1 at `.11:41451`, the frozen route/settings/reset behavior and fallback evidence.
- Do not overwrite or relabel the local `127.0.0.1:41451` Q-B evidence; a wired run gets a
  new LAN run ID/configuration.

Runtime order:

1. Five-cable LAN/IP/port preflight.
2. Camera clean to attack to recovery cycles on Samik P2.
3. Stop camera process and prove both sources released.
4. Fresh Jetson OP-TEE preflight.
5. Start Bravo `.12:51001`, Charlie `.13:51003`, then Alpha locally on Jetson.
6. Clean protocol case must produce two semantic ACKs and ACCEPTED.
7. Model-swap case must reject for the frozen reason and produce HOLD.
8. Run the separately frozen CoSys A-to-B transport proof.

No acknowledgement messages are needed. Start your task and report only a real blocker,
the frozen commit/artifact, or final test/evidence results.

---
