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

*None yet.*

All current team-direction changes have been folded into
[`SIH_2026_Build_Plan.md`](SIH_2026_Build_Plan.md). If no open override appears above, the
build plan is authoritative.

---

## Change log

<!-- Newest entries go directly below this line. -->

### 2026-08-18 — Unreal environments split into separate scenario tracks
**Status:** FOLDED
**Changed:** the team will not co-edit one shared Unreal environment through Git. Pratik
owns the dark urban / night-mode city scenario with buildings, rooftops, and adversarial
patches on rooftops or walls. Samik owns the light terrain scenario with hills, mountains,
desert, lakes, trees, and daylight visuals. Abhijan consumes both teams' live Cosys-AirSim
endpoints or exported image folders and runs the same YOLO / action / dashboard
verification pipeline.
**Makes stale:** the older assumption that M3 owns the only Unreal scene and that M4 is
unassigned deck-only support.
**Who must act:** M2 Abhijan verifies all Pratik/Samik handoff frames with YOLO and feeds
results to the dashboard. M3 Pratik delivers the primary dark urban live demo and backup
frames/video. M4 Samik delivers the secondary light terrain scenario as recorded evidence
or live Cosys-AirSim if ready. M1 Suyash keeps the protocol/event contract unchanged and
consumes either environment through the same Python-facing interfaces.
**Why:** Unreal maps/assets are binary and painful to merge. Separate scenario packs avoid
Git synchronization conflicts while still proving the same VeriSwarm Python verifier works
across multiple environments.

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
