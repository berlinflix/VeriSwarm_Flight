# Pratik to Abhijan - Controlled Adversarial Scene Support

## Current disposition

`PRATIK-CONTROLLED-OCCLUSION-01` is prepared but unvalidated. The image, clean
control, manifest and placement card are ready for printing and rehearsal. It
must not be called an accepted adversarial patch until the pinned detector and
three unchanged clean-to-attack-to-recovery cycles demonstrate a reproducible
measured effect.

## Ownership

- Pratik owns the CoSys scene copy, 3-D placement, camera alignment, POV capture
  and calibration evidence.
- Abhijan owns physical artifact handling, the attack manifest, placement card,
  expected-versus-actual oracle and projector evidence.
- Samik owns detector/application execution and retains raw program output.
- Suyash accepts the final evidence and presentation claim.

## Files to transfer to Abhijan

- `controlled_occlusion_v1.png`
- `clean_control.svg`
- `artifact_manifest.json`
- `scene_placement.template.json`
- `PRINT_AND_PLACEMENT_CARD.md`
- `SHA256SUMS`

The source bundle is under
`codebase/sim/cosys/adversarial_scene_support/controlled_occlusion_v1/`.

## Required first rehearsal

1. Obtain and hash the exact pinned detector model used on Samik P2.
2. Confirm `person` is detected from both fixed clean views at confidence 0.25
   or higher and occupies at least 0.7 of frame height.
3. Mark both camera positions, target position, artifact stand position and
   lighting state.
4. Capture clean Camera A and Camera B output.
5. Apply the artifact to Camera A only and capture raw output.
6. Remove it and capture recovery without moving cameras or changing settings.
7. Repeat the unchanged cycle three times.
8. Abhijan records actual results. The oracle may compare measured output but
   may not manufacture a dispute, abstention or PASS.

## Simulator follow-up

After the physical rehearsal, Pratik may use the same texture on visible 3-D
geometry in a new CoSys attack-mode build. That build receives a new world ID,
configuration hash and evidence directory. The frozen Q-B build and Cold Run 1
evidence remain unchanged.
