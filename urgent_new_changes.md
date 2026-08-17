# ⚠️ URGENT — READ THIS FIRST

**This file overrides [`SIH_2026_Build_Plan.md`](SIH_2026_Build_Plan.md).** Anything written
here is newer than the plan and **wins wherever the two disagree.** The plan is not
rewritten every time something changes — changes land here first, and get folded into the
plan later.

### Read order for anyone picking up this project
1. **This file** — top to bottom. Newest entries are at the top.
2. [`SIH_2026_Build_Plan.md`](SIH_2026_Build_Plan.md) — architecture, attacks, member plans.
3. [`HARDWARE_LIST.md`](HARDWARE_LIST.md) — what to buy and the gotchas.

---

## How to use this file

**Suyash** writes entries here whenever a decision changes the algorithm, the protocol, the
infrastructure, or anyone's task. Newest at the top, directly under this section.

**Everyone else** reads the top of this file before starting work each day. If an entry says
your section of the plan is stale, the entry is what you build — not the plan.

**Claude** reads this file first, before the build plan, at the start of every session. When
an entry is fully folded into `SIH_2026_Build_Plan.md`, mark it `FOLDED` rather than
deleting it — the history of why something changed is worth keeping.

### Entry template — copy this

```markdown
### YYYY-MM-DD — Short title
**Status:** OPEN
**Changed:** what is now different, concretely.
**Makes stale:** which sections/files of the plan are now wrong. Name them.
**Who must act:** M1 Suyash / M2 Abhijan / M3 Pratik / M4 — and what each does differently.
**Why:** the reasoning, so nobody re-litigates it in three weeks.
```

Set `Status: FOLDED` once the build plan has been updated to match.

---

## Open overrides

### 2026-08-18 — Formation geometry: 3 m spacing is dead, use a 5.5 m ring
**Status:** OPEN
**Changed:** peers no longer fly at the 3 m / 6 m offsets the plan specifies. The formation
is now **N = 5 on a ring of radius 5.5 m at 14 m altitude** — neighbours 6.5 m apart,
skip-one pairs 10.5 m. A new mission parameter `phi_min = 23.0` (degrees) is enforced by
the protocol.

Two bounds pin this, and both are real:
* **Too close** and peers see the same scene from the same angle. One adversarial patch
  fools all of them, and their agreement proves nothing.
* **Too far** and their camera footprints stop overlapping, so there is no shared scene to
  cross-check at all.

The feasible band is `R ∈ [5.0, 6.0] m`; 5.5 sits in the middle with margin both ways
(worst-pair overlap 0.14 against a 0.10 floor, worst-pair parallax 26° against a 23° floor).
Verified by `tests/test_angular_diversity.py::test_feasible_radius_band`.

**Makes stale:** the build plan's *"Peers at the measured 3 m and 6 m offsets (12° and 23°)"*
in the patch section; M3's Week 3 `settings.json` task; every diagram showing a line-abreast
formation.
**Who must act:**
* **M3 Pratik** — build the scene and `settings.json` for the ring, not a line. Do not
  place peers at 3 m. Use `python -m node.common` helpers or copy the pose table from
  `tests/test_angular_diversity.py`.
* **M1 Suyash** — set `phi_min` in every demo manifest.
* **M2 Abhijan** — the swarm map is a ring now, not a row.

**Why:** 3 m at 14 m is only ~12° of parallax, which is **below** the angle at which
Section 4.3 measured adversarial patches losing their grip. That is not a detail — it is the
direct cause of the silent failure in the Gazebo run, where the patch fooled all three
drones at once and the semantic layer caught nothing. The old spacing was chosen to
guarantee overlap and nobody checked the other bound.

---

### 2026-08-18 — Cameras have a pitch now; the nadir assumption was wrong
**Status:** OPEN
**Changed:** `protocol.geometry.Pose` gains a fifth field, `pitch` (camera tilt off nadir,
radians, `0` = straight down). Manifest poses accept `[x, y, z, yaw, pitch]`;
four-element poses still mean nadir, so nothing already written breaks.

**Makes stale:** any assumption that the co-visibility model matched the camera. It did not.
**Who must act:**
* **M3 Pratik** — record the actual camera pitch you set in Unreal and report it at the
  Week-1 sync. It goes in the manifest. If you tilt the camera and nobody writes the angle
  down, the overlap numbers are fiction.
* **M1 Suyash** — populate `pitch` from the sim.

**Why:** the footprint model projected a rectangle straight down and scaled it with
altitude, while the controller it feeds (`detections_to_action`) reads a *forward-looking*
scene — "climb if the obstacle sits low in the frame", empty frame means full forward. Two
forward-facing cameras' overlap is not the intersection of two ground rectangles. Because
the co-visibility gate **abstains rather than errors** when overlap looks low, a wrong
footprint would not have thrown anything: every peer would have ACKed, the console would
have been solid green, and the semantic layer would have been switched off with no
indication. The new projection reduces to the old rectangle exactly at `pitch = 0`, so
every published number is unchanged.

---

### 2026-08-18 — Depth is a required second modality
**Status:** OPEN
**Changed:** new module `perception/depth_check.py`. A drone now cross-checks its own
commanded action against measured range and flags a contradiction on its own, with no peers
and no vote.

**Makes stale:** the attack table's implication that the adversarial patch is caught only by
peer cross-verification.
**Who must act:**
* **M3 Pratik** — capture `ImageType.DepthPerspective` in the same `simGetImages` call as
  the RGB frame. It is one extra line and costs nothing. Save as `.npy` alongside the JPEGs.
* **M2 Abhijan** — new `depth` event type; render `contradicted` prominently. **`nearest_m:
  null` means "unknown", never "clear".**
* **M1 Suyash** — hardware node gets a Benewake TF-Luna (~₹2,049, UART straight onto the
  Jetson 40-pin header, no level shifter, no converter board).

**Why:** a printed patch attacks the *image*. It cannot change how far away the wall is. A
drone commanding full forward while its own rangefinder reports a surface at 8 m has
contradicted itself — and that check still works when the drone is alone and has no peers to
compare against, which is the one case the whole cross-verification design cannot cover.

---

### 2026-08-18 — Event contract is frozen; M2 is unblocked
**Status:** OPEN
**Changed:** shipped `docs/EVENT_SCHEMA.md`, `node/events.py`, `tools/mock_events.py`, and
`node/frame_source.py`. Six replayable scenarios: `honest`, `patch`, `model_swap`,
`collusion`, `unverified`, `provisioning`.

```bash
python -m tools.mock_events --scenario patch --rate 2
```

**Makes stale:** nothing — this is the Week-1 deliverable the plan already called for,
arriving late.
**Who must act:**
* **M2 Abhijan** — **start now.** Build the entire console against
  `tools/mock_events.py`. Do not wait for a running swarm; you will never need one.
  Three things in the schema are easy to render wrongly and all three mislead a judge:
  1. **Not every ACK is a pass.** `ok_no_covisibility` and `ok_no_observation` are
     *abstentions*. Draw them grey. Drawing them green claims a check that never happened.
  2. **You get one verdict per node, not one per round.** Every node tallies independently
     now. *"4 of 4 nodes independently reached REJECTED"* is the line worth showing.
  3. **`semantic_acks: 0` on an ACCEPTED means nobody checked it.** Run
     `--scenario unverified` — if that looks identical to a verified accept on your screen,
     that is the bug to fix first.
* **M3 Pratik** — your frame captures feed `FileSource`. Zero-pad filenames (`f_001.jpg`)
  or the ordering goes lexicographic.

**Why:** the console was the one deliverable with a hard dependency on the backend, and the
backend is the part most likely to slip. The contract removes the dependency entirely.

---

### 2026-08-18 — Protocol changes the console must show
**Status:** OPEN
**Changed:** three protocol gaps closed (commit `e2d3f57`), each with a visible consequence.
* **`semantic_ack_count`** on every verdict — how many peers *actually* ran the semantic
  check. An `ACCEPTED` with zero is cryptographically sound and semantically unverified;
  `fallback_action` degrades it to `EXECUTE_DEGRADED` rather than executing at full trust.
* **Peers broadcast votes and tally independently.** `PushVote` was dead code and consensus
  rested entirely on the originator — the drone under scrutiny. It could have collected
  three DISPUTEs and announced ACCEPTED with nobody the wiser.
* **`covisibility` events** carry the measured overlap and parallax, so an abstaining gate
  is readable instead of invisible.

**Makes stale:** the build plan's event-schema block — superseded by `docs/EVENT_SCHEMA.md`.
**Who must act:** **M2 Abhijan** — schema fields above. **M4** — the independent-tally line
is a strong answer to *"what if the lead drone lies?"*, which a judge will ask.
**Why:** each was a case where the system looked like it was verifying something it was not.

---

## Change log

<!-- Newest entries go directly below this line. -->

### 2026-08-15 — Member roles assigned; Windows/Mac split
**Status:** FOLDED
**Changed:** M2 is Abhijan (Mac), M3 is Pratik (Windows). M4 is still unassigned.
**Makes stale:** nothing — the build plan's *Member plans* section already reflects this.
**Who must act:** M4 seat needs a name. Everyone else: see the plan's member table.
**Why:** only one machine runs Unreal and it must be Windows. AirSim's macOS support was
never first-class and Microsoft archived the project in 2022, so the Mac dev drives the sim
over the LAN through the `airsim` RPC client on port 41451 instead of installing Unreal.

### 2026-08-15 — Repo moved out of `Life`
**Status:** FOLDED
**Changed:** all SIH work lives in `github.com/berlinflix/VeriSwarm_SIH` (this repo), local
path `C:\Users\suyas\sih\`. It was briefly committed to the `Life` repo by mistake and
removed in `2413239`.
**Makes stale:** any path referring to `Life/srip/codebase`.
**Who must act:** everyone — clone this repo, not `Life`.
**Why:** SIH work must version separately from the personal planning repo.

### 2026-08-15 — Paper codebase is frozen
**Status:** FOLDED
**Changed:** WSL `~/veriswarm/` (→ `github.com/berlinflix/veriswarm`) is **read-only**. All
edits happen in this repo's `codebase/`.
**Makes stale:** nothing.
**Who must act:** M1 — never push SIH changes to the paper repo.
**Why:** every number in `VeriSwarm_IEEE_v11.docx` must stay reproducible from untouched
code. The two codebases diverge deliberately.
