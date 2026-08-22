import assert from "node:assert/strict";
import test from "node:test";

import { presentMultiCameraStatus } from "./multiCameraPresentation.js";

function status(overrides = {}) {
  return {
    schema: "veriswarm.covis_multicam.dashboard.v1",
    feed_state: "LIVE",
    reason: "all_configured_cameras_reporting",
    updated_at_ms: 10_000,
    expected_cameras: 5,
    connected_cameras: 5,
    cameras: ["cam1", "cam2", "cam3", "cam4", "cam5"].map((name) => ({ name, status: "LIVE", detections: 1 })),
    total_pairs: 10,
    reported_pairs: 10,
    valid_pairs: 4,
    person_candidates: 2,
    disputes: 3,
    abstained: 3,
    reviews: 2,
    model_id: "sar-yolo",
    model_sha256: "f".repeat(64),
    ...overrides,
  };
}

test("all five measured cameras are presented live", () => {
  const view = presentMultiCameraStatus({ ok: true, status: status() }, 11_000);
  assert.equal(view.feedState, "LIVE");
  assert.equal(view.connectedCameras, 5);
  assert.equal(view.validPairs, 4);
  assert.equal(view.streamVisible, true);
});

test("missing cameras degrade only the multi-camera panel", () => {
  const cameras = status().cameras.map((camera, index) => index < 3 ? camera : { ...camera, status: "OFFLINE" });
  const view = presentMultiCameraStatus(status({ connected_cameras: 3, reported_pairs: 3, valid_pairs: 1, cameras }), 11_000);
  assert.equal(view.feedState, "DEGRADED");
  assert.equal(view.cameras.filter((camera) => camera.status === "OFFLINE").length, 2);
});

test("absent producer remains visibly offline", () => {
  const view = presentMultiCameraStatus({ ok: false, error: "multicamera_not_configured" }, 11_000);
  assert.equal(view.feedState, "OFFLINE");
  assert.equal(view.streamVisible, false);
});

test("old camera evidence is never labelled live", () => {
  const view = presentMultiCameraStatus(status(), 15_000, 4_000);
  assert.equal(view.feedState, "STALE");
  assert.equal(view.streamVisible, false);
});

test("malformed producer state fails closed", () => {
  const view = presentMultiCameraStatus(status({ connected_cameras: 8 }), 11_000);
  assert.equal(view.feedState, "OFFLINE");
  assert.equal(view.reason, "invalid_multicamera_status");
});

test("camera presentation does not consume or mutate Pratik rescue state", () => {
  const rescue = Object.freeze({ coverage: Object.freeze({ percent: 42 }), vehicles: Object.freeze([{ node: "alpha" }]) });
  presentMultiCameraStatus(status(), 11_000);
  assert.deepEqual(rescue, { coverage: { percent: 42 }, vehicles: [{ node: "alpha" }] });
});
