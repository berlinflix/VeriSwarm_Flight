import test from "node:test";
import assert from "node:assert/strict";

import {
  classifyVehicleFreshness,
  formatTelemetryAge,
  latestVehicleTimestamp,
  normalizeDataMode,
} from "./missionPresentation.js";

test("data mode never defaults to a live claim", () => {
  assert.equal(normalizeDataMode().key, "unverified");
  assert.equal(normalizeDataMode("LIVE_PRATIK").label, "LIVE PRATIK");
  assert.deepEqual(normalizeDataMode("MAC_FALLBACK"), {
    code: "MAC_FALLBACK",
    key: "fallback",
    label: "MAC FALLBACK",
    description: "SYNTHETIC CONTRACT-DRIVEN EVENTS",
  });
  assert.equal(normalizeDataMode("REFERENCE_REPLAY").key, "reference");
});

test("vehicle freshness uses the newest retained telemetry or link timestamp", () => {
  const vehicle = { observed_at_ms: 9_000, link_observed_at_ms: 12_000, link_state: "ONLINE" };
  assert.equal(latestVehicleTimestamp(vehicle), 12_000);
  assert.deepEqual(classifyVehicleFreshness(vehicle, 15_000, 8_000), {
    key: "live",
    label: "LIVE",
    ageMs: 3_000,
    live: true,
  });
  assert.equal(classifyVehicleFreshness(vehicle, 21_000, 8_000).key, "stale");
});

test("offline and timestamp-free vehicles fail closed", () => {
  assert.equal(classifyVehicleFreshness(null).key, "awaiting");
  assert.equal(classifyVehicleFreshness({ link_state: "ONLINE" }).key, "unverified");
  assert.equal(classifyVehicleFreshness({ link_state: "OFFLINE", observed_at_ms: 10 }).key, "offline");
  assert.equal(formatTelemetryAge(450), "NOW");
  assert.equal(formatTelemetryAge(4_900), "4s AGO");
});
