export const DEFAULT_STALE_AFTER_MS = 8_000;

function finiteTimestamp(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric > 0 ? numeric : null;
}

export function normalizeDataMode(value) {
  const normalized = String(value ?? "").trim().toUpperCase();
  if (normalized === "LIVE_PRATIK") {
    return { code: normalized, key: "live", label: "LIVE PRATIK", description: "DIRECT COSYS EVENTS" };
  }
  if (normalized === "REFERENCE_REPLAY") {
    return { code: normalized, key: "reference", label: "REFERENCE REPLAY", description: "NON-LIVE FORMAT CHECK" };
  }
  if (normalized === "MAC_FALLBACK") {
    return {
      code: normalized,
      key: "fallback",
      label: "MAC FALLBACK",
      description: "SYNTHETIC CONTRACT-DRIVEN EVENTS",
    };
  }
  return { code: normalized, key: "unverified", label: "SOURCE UNVERIFIED", description: "MODE NOT DECLARED" };
}

export function latestVehicleTimestamp(vehicle) {
  const timestamps = [
    finiteTimestamp(vehicle?.observed_at_ms),
    finiteTimestamp(vehicle?.link_observed_at_ms),
  ].filter((value) => value !== null);
  return timestamps.length ? Math.max(...timestamps) : null;
}

export function classifyVehicleFreshness(
  vehicle,
  nowMs = Date.now(),
  staleAfterMs = DEFAULT_STALE_AFTER_MS,
) {
  if (!vehicle) return { key: "awaiting", label: "AWAITING", ageMs: null, live: false };
  if (vehicle.link_state === "OFFLINE") {
    return { key: "offline", label: "OFFLINE", ageMs: null, live: false };
  }
  const timestamp = latestVehicleTimestamp(vehicle);
  if (timestamp === null) {
    return { key: "unverified", label: "UNVERIFIED", ageMs: null, live: false };
  }
  const ageMs = Math.max(0, Number(nowMs) - timestamp);
  if (!Number.isFinite(staleAfterMs) || staleAfterMs <= 0 || ageMs > staleAfterMs) {
    return { key: "stale", label: "STALE", ageMs, live: false };
  }
  return { key: "live", label: "LIVE", ageMs, live: true };
}

export function formatTelemetryAge(ageMs) {
  if (!Number.isFinite(ageMs)) return "NO TIMESTAMP";
  if (ageMs < 1_000) return "NOW";
  if (ageMs < 60_000) return `${Math.floor(ageMs / 1_000)}s AGO`;
  return `${Math.floor(ageMs / 60_000)}m AGO`;
}
