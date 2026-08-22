import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { buildCellMissionView } from "./cellMissionView.js";

const contract = JSON.parse(readFileSync(new URL(
  "../../sim/cosys/factorycity/factorycity_joint_movement_contract.development.json",
  import.meta.url,
), "utf8"));

const state = {
  assignments: [
    { node: "alpha", sector_id: "sector-alpha", cell_ids: ["route_cell_00", "route_cell_05"] },
    { node: "bravo", sector_id: "sector-bravo", cell_ids: ["route_cell_01", "route_cell_06"] },
  ],
  assignment_conflicts: [],
  coverage: {
    in_progress_cell_ids: ["route_cell_05"],
    completed_cell_ids: ["route_cell_00"],
    blocked_cell_ids: [],
    cell_state_conflicts: [],
  },
  coverage_by_sector: [{
    node: "alpha",
    sector_id: "sector-alpha",
    in_progress_cell_ids: ["route_cell_05"],
    completed_cell_ids: ["route_cell_00"],
    blocked_cell_ids: [],
  }],
  reassignments: [],
  movement_safety: [],
};

test("heatmap geometry and colors come from exact IDs plus immutable configuration", () => {
  const view = buildCellMissionView(state, contract);
  assert.equal(view.ready, true);
  assert.equal(view.cells.length, 10);
  assert.equal(view.cells[0].id, "route_cell_00");
  assert.equal(view.cells[0].widthPercent, 10);
  assert.equal(view.cells[0].state, "COMPLETED");
  assert.equal(view.cells[5].state, "IN_PROGRESS");
  assert.equal(view.scoreboard.completed, 1);
  assert.equal(view.scoreboard.inProgress, 1);
});

test("unknown IDs and inconsistent ownership fail visibly instead of receiving a color", () => {
  const unsafe = structuredClone(state);
  unsafe.assignments[1].cell_ids.push("unknown-cell");
  unsafe.coverage_by_sector[0].blocked_cell_ids = ["route_cell_01"];
  const view = buildCellMissionView(unsafe, contract);
  assert.ok(view.issues.some((issue) => issue.code === "unknown_assignment_cell"));
  assert.ok(view.issues.some((issue) => issue.code === "coverage_not_owned"));
  assert.equal(view.cells.find((cell) => cell.id === "route_cell_01").state, "CONFLICT");
});

test("reassignment transitions must originate from the current configured owner", () => {
  const unsafe = structuredClone(state);
  unsafe.reassignments = [{
    event_id: "bad-transfer",
    from_node: "charlie",
    to_node: "bravo",
    cell_ids: ["route_cell_00"],
  }];
  const view = buildCellMissionView(unsafe, contract);
  assert.ok(view.issues.some((issue) => issue.code === "invalid_reassignment_owner"));
  assert.equal(view.cells[0].state, "CONFLICT");
});

test("a valid reassignment preserves earlier coverage and reconstructs the current owner", () => {
  const reassigned = structuredClone(state);
  reassigned.reassignments = [{
    event_id: "valid-transfer",
    from_node: "alpha",
    to_node: "bravo",
    cell_ids: ["route_cell_05"],
  }];
  const view = buildCellMissionView(reassigned, contract);
  assert.equal(view.cells.find((cell) => cell.id === "route_cell_05").owner, "bravo");
  assert.ok(!view.issues.some((issue) => issue.cellId === "route_cell_05"));
});

test("movement evidence distinguishes blocked cells from vehicle collisions", () => {
  const movementState = structuredClone(state);
  movementState.movement_safety = [
    { event_id: "blocked", cell_id: "route_cell_05", result: "TERMINAL_CELL" },
    { event_id: "collision", cell_id: "route_cell_01", result: "TERMINAL_VEHICLE" },
  ];
  const view = buildCellMissionView(movementState, contract);
  assert.deepEqual(view.movement.map((event) => event.terminalLabel), [
    "TERMINAL CELL BLOCKAGE",
    "TERMINAL VEHICLE COLLISION",
  ]);
});
