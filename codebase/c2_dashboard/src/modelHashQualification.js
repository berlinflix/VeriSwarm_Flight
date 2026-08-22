/** Return true only when retained clean evidence is safe to present as approved. */
export function isApprovedCleanEvidence(evidence) {
  return evidence?.dashboard_proof?.proof_valid === true
    && evidence?.actual?.outcome === "ACCEPTED"
    && Number(evidence?.actual?.semantic_acks ?? 0) >= 2;
}
