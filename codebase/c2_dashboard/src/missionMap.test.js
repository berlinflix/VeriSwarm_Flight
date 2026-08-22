import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { buildCellMissionView } from "./cellMissionView.js";
import { buildMissionMap } from "./missionMap.js";

const contract = JSON.parse(readFileSync(new URL(
  "../../sim/cosys/factorycity/factorycity_joint_movement_contract.development.json",
  import.meta.url,
), "utf8"));

test("top view uses exact cell states and only genuine vehicle positions", () => {
  const state = {
    assignments: [{ node: "alpha", cell_ids: ["route_cell_00", "route_cell_05"] }],
    assignment_conflicts: [],
    coverage: {
      in_progress_cell_ids: [],
      completed_cell_ids: ["route_cell_00"],
      blocked_cell_ids: [],
      cell_state_conflicts: [],
    },
    coverage_by_sector: [],
    reassignments: [],
    movement_safety: [],
  };
  const view = buildCellMissionView(state, contract);
  const map = buildMissionMap(view, contract, [
    { node: "alpha", position_ned: [-20, 4, -10] },
    { node: "bravo", position_ned: null },
  ]);
  assert.equal(map.ready, true);
  assert.equal(map.cells.length, 10);
  assert.equal(map.cells[0].state, "COMPLETED");
  assert.deepEqual(map.vehicles.map((vehicle) => vehicle.node), ["alpha"]);
});
