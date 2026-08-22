import test from "node:test";
import assert from "node:assert/strict";

import { isApprovedCleanEvidence } from "./modelHashQualification.js";

const cleanEvidence = (overrides = {}) => ({
  dashboard_proof: { proof_valid: true },
  actual: { outcome: "ACCEPTED", semantic_acks: 2 },
  ...overrides,
});

test("approved clean evidence requires a valid proof and semantic quorum", () => {
  assert.equal(isApprovedCleanEvidence(cleanEvidence()), true);
});

test("accepted clean evidence with two acknowledgements fails when proof is invalid", () => {
  const evidence = cleanEvidence({ dashboard_proof: { proof_valid: false } });
  assert.equal(isApprovedCleanEvidence(evidence), false);
});

test("valid proof does not bypass outcome or semantic quorum", () => {
  assert.equal(
    isApprovedCleanEvidence(cleanEvidence({ actual: { outcome: "REJECTED", semantic_acks: 2 } })),
    false,
  );
  assert.equal(
    isApprovedCleanEvidence(cleanEvidence({ actual: { outcome: "ACCEPTED", semantic_acks: 1 } })),
    false,
  );
});

test("missing or malformed evidence fails closed", () => {
  assert.equal(isApprovedCleanEvidence(null), false);
  assert.equal(
    isApprovedCleanEvidence(cleanEvidence({ actual: { outcome: "ACCEPTED", semantic_acks: "invalid" } })),
    false,
  );
});
