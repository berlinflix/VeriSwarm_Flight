const WIDTH = 800;
const HEIGHT = 330;
const PADDING = 48;

function ned2(value) {
  return Array.isArray(value)
    && value.length >= 2
    && Number.isFinite(Number(value[0]))
    && Number.isFinite(Number(value[1]));
}

function interpolate(start, end, fraction) {
  return [
    start[0] + (end[0] - start[0]) * fraction,
    start[1] + (end[1] - start[1]) * fraction,
  ];
}

/** Build an NED top-view overlay without inventing missing vehicle positions. */
export function buildMissionMap(
  missionView,
  movementConfig,
  vehicles = [],
  people = [],
  hazards = [],
) {
  const start = movementConfig?.route?.centroid_start_ned_m;
  const end = movementConfig?.route?.centroid_end_ned_m;
  if (!ned2(start) || !ned2(end)) {
    return {
      ready: false,
      width: WIDTH,
      height: HEIGHT,
      cells: [],
      vehicles: [],
      people: [],
      hazards: [],
    };
  }

  const geofence = movementConfig?.command_limits?.enroute_geofence_ned_m;
  const hasGeofence = [geofence?.x_min, geofence?.x_max, geofence?.y_min, geofence?.y_max]
    .every((value) => Number.isFinite(Number(value)));
  const northValues = hasGeofence
    ? [Number(geofence.x_min), Number(geofence.x_max)]
    : [Number(start[0]), Number(end[0])];
  const eastValues = hasGeofence
    ? [Number(geofence.y_min), Number(geofence.y_max)]
    : [Number(start[1]), Number(end[1])];
  const northSpan = Math.max(1, Math.max(...northValues) - Math.min(...northValues));
  const eastSpan = Math.max(1, Math.max(...eastValues) - Math.min(...eastValues));
  const worldPadding = hasGeofence ? 0 : Math.max(6, Math.max(northSpan, eastSpan) * 0.08);
  const minNorth = Math.min(...northValues) - worldPadding;
  const maxNorth = Math.max(...northValues) + worldPadding;
  const minEast = Math.min(...eastValues) - worldPadding;
  const maxEast = Math.max(...eastValues) + worldPadding;
  const scale = Math.min(
    (WIDTH - PADDING * 2) / (maxEast - minEast),
    (HEIGHT - PADDING * 2) / (maxNorth - minNorth),
  );
  const project = (position) => ({
    x: WIDTH / 2 + (Number(position[1]) - (minEast + maxEast) / 2) * scale,
    y: HEIGHT / 2 - (Number(position[0]) - (minNorth + maxNorth) / 2) * scale,
  });

  const cells = (missionView?.cells ?? []).map((cell) => {
    const from = project(interpolate(start, end, cell.start));
    const to = project(interpolate(start, end, cell.end));
    const dx = to.x - from.x;
    const dy = to.y - from.y;
    const length = Math.max(1, Math.hypot(dx, dy));
    const nx = (-dy / length) * 12;
    const ny = (dx / length) * 12;
    return {
      ...cell,
      points: [
        `${from.x + nx},${from.y + ny}`,
        `${to.x + nx},${to.y + ny}`,
        `${to.x - nx},${to.y - ny}`,
        `${from.x - nx},${from.y - ny}`,
      ].join(" "),
      label: project(interpolate(start, end, (cell.start + cell.end) / 2)),
    };
  });

  const projectedVehicles = vehicles.flatMap((vehicle) => {
    if (!ned2(vehicle?.position_ned)) return [];
    return [{ ...vehicle, ...project(vehicle.position_ned) }];
  });
  const projectedPeople = people.flatMap((person) => {
    if (!ned2(person?.position_ned)) return [];
    return [{ ...person, ...project(person.position_ned) }];
  });
  const projectedHazards = hazards.flatMap((hazard) => {
    if (!ned2(hazard?.position_ned)) return [];
    return [{ ...hazard, ...project(hazard.position_ned) }];
  });

  return {
    ready: true,
    width: WIDTH,
    height: HEIGHT,
    cells,
    vehicles: projectedVehicles,
    people: projectedPeople,
    hazards: projectedHazards,
    start: project(start),
    end: project(end),
  };
}
