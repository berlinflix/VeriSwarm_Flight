# Abhijan — disaster scenario and evaluation design plan

## Mission

Design the disaster mission, ground truth and expected outcomes. Pratik owns the Unreal/
CoSys implementation; you own what must exist, where it is, what it means and how the
result is scored.

## Scenario: Operation Varuna

Use a derived copy of the working FactoryCity environment. Do not switch simulators in a
36-hour sprint and do not modify Pratik's protected original project.

Minimum layout:

- one safe launch/landing staging area;
- one bounded urban search polygon divided into five sectors;
- three person targets:
  - P1 visible on an elevated/open surface;
  - P2 partially occluded beside rubble but visible from at least one route;
  - P3 adjacent to floodwater or a blocked access route;
- one flood/water polygon;
- one road blocked by static rubble;
- one depth-visible navigation obstacle requiring HOLD/bypass;
- one fire/smoke visual zone, labelled simulator truth unless an accepted model detects it;
- at least one safe responder-access route.

## H0–H3: scenario contract

- [ ] Create `SCENARIO_MANIFEST.json` with schema/version/scenario ID.
- [ ] Declare the exact NED origin and Unreal-to-NED transform.
- [ ] Provide search polygon, launch/landing bounds and sector seed.
- [ ] Assign stable, human-readable actor names.
- [ ] Define person/hazard positions and polygons in ground truth.
- [ ] Define expected detection visibility by camera route, not desired model verdict.
- [ ] Define mission start, discovery and completion events.
- [ ] Define clean reset and abort behavior.
- [ ] Hand the frozen contract to Pratik by H3.

## H3–H10: world design support

- [ ] Work beside Pratik while he builds the derived level; do not edit his controller.
- [ ] Use existing/licensed Unreal assets or simple primitives. Record asset source/license.
- [ ] Prefer static/skeletal meshes for actors that require Cosys instance segmentation;
      Cosys documents limitations for Landscape, foliage and brush objects.
- [ ] Add Niagara smoke/fire only as a visual effect and truth-tagged hazard.
- [ ] Keep rubble collision meshes simple and inspect them visibly.
- [ ] Place water so the safe route remains possible.
- [ ] Capture overview and each target from the planned RGB route.
- [ ] If synthetic infrared is used, provide temperature/emissivity/response manifest and
      label it synthetic. Do not present recolored segmentation as a real thermal camera.

## H8–H16: truth and evaluator

- [ ] Export exact actor pose, category, severity and polygon/box ground truth.
- [ ] Create an evaluator that compares mission output to truth after the run; truth must
      not be importable by the autonomy process.
- [ ] Score time-to-first-person, person recall, duplicate alerts, hazard recall, coverage,
      route safety, collision count and mission completion.
- [ ] Add a truth-leakage test: autonomy input paths must not contain truth files.
- [ ] Create three held-out world variants by changing target placement/lighting; use one
      for integration, two for evaluation.

## H16–H26: attack and resilience beat

- [ ] Reuse the existing model-hash attack as the security event.
- [ ] Preserve all prior attack commits, fixtures, patches and evidence; new rescue attack
      runs receive new immutable run IDs.
- [ ] Add replay/stale, peer-timeout and adversarial-patch cases only when their exact
      expected reason/HOLD contract is frozen.
- [ ] Define which drone is quarantined and which unvisited cells must be reassigned.
- [ ] Ensure the dashboard tells the story as “unapproved perception model rejected,” not
      “the system proved the alternative model malicious.”
- [ ] Keep the physical multi-camera/occlusion demonstration separate unless the accepted
      semantic camera cycle passes.
- [ ] Describe the result as resistance to the demonstrated attacks, never
      “adversarial-proof” or “tamper-proof.”
- [ ] Prepare one roadmap sentence explaining that border surveillance can reuse the trust
      and autonomy core after domain-specific model and operational validation.

## H26–H36: evidence and presentation

- [ ] Freeze world/settings/truth hashes and two reset screenshots.
- [ ] Provide a one-page map showing sectors, targets, hazards and safe route.
- [ ] Review both cold runs against truth without changing expected outcomes.
- [ ] Prepare a 30-second scenario narration centered on responder value.
- [ ] Preserve failed world builds/runs separately; never overwrite evidence.

## Acceptance gate

- [ ] World boots twice from cold state with the same actors and transforms.
- [ ] All five drones spawn collision-free at A.
- [ ] Person targets are visible from declared paths.
- [ ] At least one traversable route exists.
- [ ] Collision geometry matches visible geometry.
- [ ] Truth is hash-frozen and inaccessible to autonomy.
- [ ] Reset removes all state and restores exact starting poses.
- [ ] No unsupported AI hazard claim appears in labels or narration.
