# STATUS — Ayush multi-camera dashboard integration

## Implemented in code

- Ported the current 2--5 camera visualizer and retained the geometry-required
  appearance safety gate.
- Added a read-only in-memory status/MJPEG bridge to the Samik camera process.
- Added durable image-space rescue observation conversion with separate perception
  sources, persistent sequences, bounded publishing and exact replay identities.
- Added Abhijan dashboard proxying and a responsive 70/30 multi-camera panel directly
  above the cell heatmap/movement/reassignment section.
- Preserved Pratik's rescue, telemetry, authorization and video paths unchanged.
- Added visible `LIVE`, `DEGRADED`, `STALE` and `OFFLINE` handling plus expanded view.

## Verification completed

- Python compilation for all changed camera/adapter modules.
- Complete Python suite, including camera, node, protocol, rescue, Pratik-path and
  qualification regressions: 583 passed, 3 skipped.
- Dashboard presentation tests: 19 passed, including live/degraded/offline/stale/
  malformed producer states and independence from Pratik state.
- Dashboard dependency install, production Vite build and shell launcher syntax checks.
- Desktop browser check confirmed the 70/30 layout, uncropped composite container,
  exact offline wording and placement immediately above the heatmap/movement panel.
- Live browser check used the supplied 1600x999 Samik composite and confirmed
  `LIVE`, `5/5`, `4/10`, two person candidates and three disputes without cropping.
- Responsive browser check confirmed stacked composite/sidebar rendering at 760 px
  with no horizontal overflow; expanded mode restores the exact saved scroll offset.

## Verification still required

- Actual Samik camera/model run through Abhijan's Mac proxy.
- Combined retained run with Pratik telemetry and Samik perception arriving together.

No live hardware result is claimed until the final two retained runs exist.
