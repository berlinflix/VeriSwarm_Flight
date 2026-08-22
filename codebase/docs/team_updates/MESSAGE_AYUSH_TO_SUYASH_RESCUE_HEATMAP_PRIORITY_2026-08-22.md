# MESSAGE — AYUSH TO SUYASH

Date: 2026-08-22 IST

Subject: Proposal for search-progress heatmap and deterministic rescue-priority view

Branch: `ayush/sih26177-perception-support`

This is a bounded dashboard/product proposal for the current search-and-rescue mission.
It should reuse the existing rescue event log and alert pipeline rather than create a
second source of mission state.

## 1. Search-progress heatmap

Add a map layer that distinguishes:

- unsearched cells;
- cells currently being searched;
- completely covered cells;
- obstructed or unreachable cells;
- person-candidate and hazard markers;
- the drone that currently owns each sector; and
- cells reassigned after failure, quarantine or loss of availability.

The heatmap should be a deterministic projection of existing coverage, assignment,
hazard, authorization and vehicle-state events. The dashboard must not infer coverage
from animation or maintain a separate mutable truth store.

Suggested visual states:

| State | Suggested rendering |
|---|---|
| `UNSEARCHED` | neutral grey |
| `IN_PROGRESS` | blue with active-drone label |
| `COVERED` | green |
| `BLOCKED` / `UNREACHABLE` | hatched red/amber |
| `REASSIGNED` | purple outline plus previous/current owner |

Useful summary counters are coverage percentage, cells remaining, blocked cells and
reassignment count. Clicking a cell should reveal owner, status, last event time and
evidence/event IDs.

### Heatmap acceptance beat

1. Five sectors begin unsearched and show their owners.
2. Coverage events visibly move cells through in-progress to covered.
3. A blocked cell is marked without being counted as covered.
4. One drone is quarantined or unavailable.
5. Only its unfinished cells change owner and receive the reassigned visual state.
6. Dashboard state reconstructed by replay matches the live state exactly.

This gives judges immediate visual proof that the drones are performing autonomous
coordinated search rather than independent/random movement.

## 2. Deterministic Rescue Priority Engine

Add a separate, explainable responder-priority code to each candidate/hazard:

- `P1`: person candidate near fire, flood, blocked structure or another measured severe
  hazard;
- `P2`: person candidate supported by multiple independent positive observations;
- `P3`: single-view person candidate requiring confirmation;
- `H1`: measured hazard affecting access or mission routing; and
- `REVIEW`: low-confidence, stale, geometrically inconsistent or conflicting evidence.

Every priority must include machine-readable reason codes and a short human-readable
explanation. Example: `P1 — person candidate 4.2 m from mapped flood boundary; supported
by alpha and bravo`.

The engine must not diagnose injury, claim a confirmed survivor, or silently delete a
positive observation. It orders responder review; it does not control flight directly.

### Important compatibility point

The current survivor-first policy says one detector-thresholded positive immediately
becomes a `HIGH` unconfirmed alert. Preserve that safety behavior. Implement `P1/P2/P3`
as an orthogonal responder-ordering field such as `rescue_priority_code`, or version the
shared alert contract explicitly after review. Do not downgrade or suppress the existing
`HIGH` alert merely because it is single-view.

### Priority acceptance beat

1. A single-view positive remains visible and receives `P3`, with confirmation requested.
2. A second independent positive promotes it to `P2` without creating a duplicate marker.
3. A measured nearby hazard promotes ordering to `P1` with the exact distance/reason.
4. A conflicting or stale observation is labelled `REVIEW` while the original positive
   remains visible.
5. Replaying the same event log produces the same ordering and explanations.

## Suggested ownership

- Suyash: priority schema, deterministic projection, report and dashboard integration.
- Pratik: coverage-cell, ownership, blocked/unreachable and reassignment events.
- Samik: person/hazard observations and independent-view support fields.
- Abhijan: withheld-truth evaluation, replay equivalence and priority/oracle tests.
- Ayush: UI review, multi-camera presentation mapping and independent acceptance checks.

## Recommendation

Adopt the heatmap after the existing end-to-end coverage events are stable. Adopt the
priority field only after reconciling it with the current survivor-first `HIGH` policy.
Neither proposal should block the P0 person-detection, geolocation, obstacle-HOLD and
reassignment thin slice.
