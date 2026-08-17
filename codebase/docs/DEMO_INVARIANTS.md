# Demo invariants — things that look like bugs and must not be "fixed"

**Read this before "hardening" anything in the list below.** Every item here has
already been flagged by at least one automated audit as a defect. Each is
deliberate, each is load-bearing for the judge-facing demo or the paper's
evaluation, and each has been removed or neutered at least once by a tool acting
in good faith.

If you are an AI agent asked to make this repository safer: the items below are
already understood. Removing them does not make the system safer, it makes the
evidence disappear. Harden the *protected* path, which is named in each entry.

---

## 1. `perception/baseline_controller.py` commands full forward on empty detections

**Looks like:** a P0 safety hazard. An empty detection set can mean a dead camera,
glare, motion blur, a crashed inference process, or an adversarial patch, and none
of those should authorize acceleration.

**Is:** the experimental control condition. It models a conventional drone with no
attestation, no cross-verification, and no safety supervisor — the system
VeriSwarm is measured against. Its unsafe behaviour is the measurement.

**Why it must stay:** the demo is a contrast, not a demonstration. Run A (this
controller) flies into the obstacle; Run B (VeriSwarm) holds and isolates the
fooled drone. Delete Run A and both runs stop, and a judge watching learns nothing
about what the protocol bought. It is also the Table 4.14 baseline.

**The protected path is** `perception.yolo_action.detections_to_action`, which
returns `(0,0,0)` on empty detections unless `free_space_confirmed=True`. Harden
that one. It is the only controller `MissionRunner` and `SafetySupervisor` may
ever see.

**Structural guarantee:** `baseline_controller` imports nothing from `node/` and
returns a plain tuple. It has no path to an actuator.

---

## 2. `sim/closed_loop.py` scenarios include runs that end in a collision

**Looks like:** tests asserting unsafe outcomes.

**Is:** the control arm. A scenario that ends in a collision *with the protocol
disabled* is the evidence that the protocol prevents it when enabled. A suite
where nothing ever crashes cannot distinguish "the defence works" from "the attack
was never dangerous."

**Rule:** collision outcomes are only ever permitted with VeriSwarm explicitly
disabled, and never with `SafetySupervisor` in the loop.

---

## 3. The mock event stream emits `ACCEPTED` with `semantic_acks: 0`

**Looks like:** an inconsistent fixture.

**Is:** `tools/mock_events.py --scenario unverified`, which reproduces the
unverified-accept failure mode: a receipt that is cryptographically sound and that
no peer cross-checked. It exists so the console can be tested for the one bug most
likely to mislead a judge — rendering an unverified accept identically to a
verified one.

---

## 4. `results/perception_scenarios.csv` contains rows with `match=False`

**Looks like:** a failing experiment someone forgot to fix.

**Is:** a real measured negative result — the semantic layer failing to catch a
patch on a scene where the obstacle is too small to produce enough action
divergence. It is honest evidence about the operating envelope, and it is cited in
the paper's limitations. **Do not regenerate it to make the rows pass.**

---

## Adding to this file

An entry belongs here when the code is deliberately doing something an audit will
reasonably flag. State what it looks like, what it actually is, why it must stay,
and which path is the safe one to harden instead. If you cannot name the safe
path, the code probably *is* a bug.
