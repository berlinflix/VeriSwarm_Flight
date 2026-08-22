export const MULTICAM_SCHEMA = "veriswarm.covis_multicam.dashboard.v1";

const EMPTY = Object.freeze({
  schema: MULTICAM_SCHEMA,
  feedState: "OFFLINE",
  reason: "waiting_for_camera_producer",
  expectedCameras: 5,
  connectedCameras: 0,
  cameras: [],
  totalPairs: 10,
  reportedPairs: 0,
  validPairs: 0,
  personCandidates: 0,
  disputes: 0,
  abstained: 0,
  reviews: 0,
  modelId: null,
  modelSha256: null,
  updatedAtMs: null,
  ageMs: null,
  streamVisible: false,
  limitation: "2-D evidence only; person candidates require responder confirmation",
});

function boundedInteger(value, maximum) {
  const number = Number(value);
  return Number.isInteger(number) && number >= 0 && number <= maximum ? number : null;
}

export function presentMultiCameraStatus(payload, nowMs = Date.now(), staleAfterMs = 4_000) {
  const status = payload?.status ?? payload;
  if (!status || status.schema !== MULTICAM_SCHEMA) {
    return { ...EMPTY, reason: payload?.error ?? "waiting_for_camera_producer" };
  }
  const expected = boundedInteger(status.expected_cameras, 5);
  const connected = boundedInteger(status.connected_cameras, 5);
  const totalPairs = boundedInteger(status.total_pairs, 10);
  const validPairs = boundedInteger(status.valid_pairs, 10);
  const reportedPairs = boundedInteger(status.reported_pairs, 10);
  const disputes = boundedInteger(status.disputes, 10);
  const abstained = boundedInteger(status.abstained, 10);
  const reviews = boundedInteger(status.reviews, 10);
  const candidates = boundedInteger(status.person_candidates, 10_000);
  const rawUpdatedAtMs = status.updated_at_ms;
  const updatedAtMs = rawUpdatedAtMs === null || rawUpdatedAtMs === undefined ? null : Number(rawUpdatedAtMs);
  const cameras = Array.isArray(status.cameras) ? status.cameras : null;
  const valid = expected !== null
    && expected >= 2
    && connected !== null
    && connected <= expected
    && totalPairs === expected * (expected - 1) / 2
    && reportedPairs !== null
    && reportedPairs <= totalPairs
    && validPairs !== null
    && validPairs <= reportedPairs
    && disputes !== null
    && abstained !== null
    && reviews !== null
    && candidates !== null
    && cameras !== null
    && cameras.length === expected
    && (connected === 0 || (Number.isFinite(updatedAtMs) && updatedAtMs > 0));
  if (!valid) {
    return { ...EMPTY, reason: "invalid_multicamera_status" };
  }
  const ageMs = Number.isFinite(updatedAtMs) ? Math.max(0, nowMs - updatedAtMs) : null;
  let feedState = connected === 0 ? "OFFLINE" : connected < expected ? "DEGRADED" : "LIVE";
  if (ageMs > staleAfterMs && connected > 0) feedState = "STALE";
  return {
    schema: status.schema,
    feedState,
    reason: feedState === "STALE" ? "camera_evidence_stale" : String(status.reason ?? "status_reason_unavailable"),
    expectedCameras: expected,
    connectedCameras: connected,
    cameras: cameras.map((camera) => ({
      name: String(camera?.name ?? "unknown"),
      status: camera?.status === "LIVE" ? "LIVE" : "OFFLINE",
      detections: boundedInteger(camera?.detections, 10_000) ?? 0,
      detectorError: camera?.detector_error ? String(camera.detector_error) : null,
    })),
    totalPairs,
    reportedPairs,
    validPairs,
    personCandidates: candidates,
    disputes,
    abstained,
    reviews,
    modelId: status.model_id ? String(status.model_id) : null,
    modelSha256: typeof status.model_sha256 === "string" ? status.model_sha256 : null,
    updatedAtMs,
    ageMs,
    streamVisible: feedState === "LIVE" || feedState === "DEGRADED",
    limitation: "2-D homography-projected evidence; not calibrated stereo or 3-D",
  };
}

export function formatMultiCameraAge(ageMs) {
  if (!Number.isFinite(ageMs)) return "never";
  if (ageMs < 1_000) return `${Math.round(ageMs)}ms`;
  return `${Math.floor(ageMs / 1_000)}s`;
}
