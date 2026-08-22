const CELL_STATE_FIELDS = [
  ["in_progress_cell_ids", "IN_PROGRESS"],
  ["completed_cell_ids", "COMPLETED"],
  ["blocked_cell_ids", "BLOCKED"],
];

function cellIds(value) {
  return Array.isArray(value) ? value.filter((item) => typeof item === "string") : [];
}

function issueKey(issue) {
  return [issue.code, issue.cellId ?? "", issue.detail ?? ""].join(":");
}

/**
 * Build the read-only heatmap model from retained state and immutable geometry.
 * Counts never choose a cell color; only explicit cell-ID lists do.
 */
export function buildCellMissionView(state, movementConfig) {
  const issues = [];
  const addIssue = (code, cellId, detail) => issues.push({ code, cellId, detail });
  const geometryRows = movementConfig?.search_cells?.cells;
  if (!Array.isArray(geometryRows) || geometryRows.length === 0) {
    return {
      ready: false,
      cells: [],
      issues: [{ code: "movement_geometry_unavailable", cellId: null, detail: "immutable movement configuration missing" }],
      scoreboard: { assigned: 0, inProgress: 0, completed: 0, blocked: 0, conflicts: 1 },
      movement: [],
    };
  }

  const geometry = new Map();
  for (const row of geometryRows) {
    const id = row?.id;
    const start = Number(row?.start_fraction);
    const end = Number(row?.end_fraction);
    if (typeof id !== "string" || geometry.has(id) || !Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end > 1 || end <= start) {
      addIssue("invalid_cell_geometry", typeof id === "string" ? id : null, "invalid or duplicate immutable geometry row");
      continue;
    }
    geometry.set(id, { id, start, end, configuredOwner: row.owner });
  }

  const known = new Set(geometry.keys());
  const ownerByCell = new Map([...geometry.values()].map((cell) => [cell.id, cell.configuredOwner]));
  const validOwnersByCell = new Map(
    [...geometry.values()].map((cell) => [cell.id, new Set([cell.configuredOwner])]),
  );
  for (const reassignment of state?.reassignments ?? []) {
    const reassigned = cellIds(reassignment?.cell_ids);
    if (!reassigned.length) {
      addIssue("reassignment_missing_cell_ids", null, reassignment?.event_id ?? "legacy count-only reassignment");
      continue;
    }
    for (const id of reassigned) {
      if (!known.has(id)) {
        addIssue("unknown_reassignment_cell", id, reassignment.event_id);
        continue;
      }
      if (ownerByCell.get(id) !== reassignment.from_node) {
        addIssue("invalid_reassignment_owner", id, `${reassignment.from_node}->${reassignment.to_node}`);
        continue;
      }
      ownerByCell.set(id, reassignment.to_node);
      validOwnersByCell.get(id).add(reassignment.to_node);
    }
  }

  const emittedOwners = new Map();
  for (const assignment of state?.assignments ?? []) {
    const assigned = cellIds(assignment?.cell_ids);
    if (!assigned.length) {
      addIssue("assignment_missing_cell_ids", null, assignment?.node ?? "unknown node");
      continue;
    }
    for (const id of assigned) {
      if (!known.has(id)) {
        addIssue("unknown_assignment_cell", id, assignment.node);
        continue;
      }
      const owners = emittedOwners.get(id) ?? new Set();
      owners.add(assignment.node);
      emittedOwners.set(id, owners);
    }
  }

  for (const [id, owners] of emittedOwners) {
    if (owners.size > 1) {
      addIssue("assignment_conflict", id, [...owners].sort().join(","));
    } else if (!validOwnersByCell.get(id)?.has([...owners][0])) {
      addIssue("assignment_owner_mismatch", id, `ownership chain excludes ${[...owners][0]}`);
    }
  }
  for (const conflict of state?.assignment_conflicts ?? []) {
    addIssue("assignment_conflict", conflict.cell_id, cellIds(conflict.owners).join(","));
  }

  const stateByCell = new Map();
  const coverage = state?.coverage ?? {};
  for (const [field, cellState] of CELL_STATE_FIELDS) {
    for (const id of cellIds(coverage[field])) {
      if (!known.has(id)) {
        addIssue("unknown_coverage_cell", id, field);
        continue;
      }
      if (stateByCell.has(id) && stateByCell.get(id) !== cellState) {
        addIssue("cell_state_conflict", id, `${stateByCell.get(id)},${cellState}`);
        stateByCell.set(id, "CONFLICT");
      } else {
        stateByCell.set(id, cellState);
      }
    }
  }
  for (const id of cellIds(coverage.cell_state_conflicts)) {
    addIssue("cell_state_conflict", id, "collector projection conflict");
    if (known.has(id)) stateByCell.set(id, "CONFLICT");
  }

  for (const record of state?.coverage_by_sector ?? []) {
    for (const [field] of CELL_STATE_FIELDS) {
      for (const id of cellIds(record[field])) {
        if (known.has(id) && !validOwnersByCell.get(id)?.has(record.node)) {
          addIssue("coverage_not_owned", id, `${record.node}:${field}`);
          stateByCell.set(id, "CONFLICT");
        }
      }
    }
  }

  const movement = (state?.movement_safety ?? []).map((event) => {
    if (!known.has(event.cell_id)) addIssue("unknown_movement_cell", event.cell_id, event.event_id);
    const terminalLabel = event.result === "TERMINAL_CELL"
      ? "TERMINAL CELL BLOCKAGE"
      : event.result === "TERMINAL_VEHICLE"
        ? "TERMINAL VEHICLE COLLISION"
        : "NON-TERMINAL MOVEMENT";
    return { ...event, terminalLabel };
  });

  const conflictCells = new Set(issues.filter((issue) => issue.cellId).map((issue) => issue.cellId));
  const cells = [...geometry.values()]
    .sort((left, right) => left.start - right.start || left.id.localeCompare(right.id))
    .map((cell) => ({
      ...cell,
      owner: ownerByCell.get(cell.id),
      state: conflictCells.has(cell.id)
        ? "CONFLICT"
        : stateByCell.get(cell.id) ?? (emittedOwners.has(cell.id) ? "ASSIGNED" : "UNREPORTED"),
      widthPercent: (cell.end - cell.start) * 100,
    }));

  const uniqueIssues = [...new Map(issues.map((issue) => [issueKey(issue), issue])).values()];
  const count = (value) => cells.filter((cell) => cell.state === value).length;
  return {
    ready: true,
    cells,
    issues: uniqueIssues,
    movement,
    scoreboard: {
      assigned: cells.filter((cell) => !["UNREPORTED", "CONFLICT"].includes(cell.state)).length,
      inProgress: count("IN_PROGRESS"),
      completed: count("COMPLETED"),
      blocked: count("BLOCKED"),
      conflicts: uniqueIssues.length,
    },
  };
}
