# MESSAGE — AYUSH TO ABHIJAN AND SUYASH

Date: 2026-08-22 IST

Subject: Proposal for read-only command-centre queries, judge scoreboard and search heatmap

Branch: `ayush/sih26177-perception-support`

This extends the earlier heatmap/priority proposal with two bounded presentation features.
All displayed state must come from the retained rescue event log or from Abhijan's isolated
post-run oracle. Nothing in this proposal may create or release a flight command.

## 1. Optional wow feature: read-only natural-language command centre

Support a small allowlist of operator questions:

- `Show the highest-priority person candidate.`
- `Which sectors remain unsearched?`
- `Why was Alpha quarantined?`
- `Generate the incident summary.`

Recommended flow:

```text
operator text
  -> allowlisted intent + validated read-only parameters
  -> deterministic event-log projection/report query
  -> structured answer with event/evidence IDs
  -> optional natural-language formatting
```

### Safety boundary

- The interface reads the append-only event log and existing report projection only.
- It has no command-sink, simulator-control, mission-authority or actuator credentials.
- It cannot call takeoff, land, move, replan, assign, quarantine or authorization APIs.
- Unknown or ambiguous requests are refused; they are never converted into actions.
- Every factual answer cites the mission ID, last processed sequence/time and supporting
  event/evidence IDs.
- The answer must say `unknown` or `not measured` when the event log lacks evidence.
- `Generate the incident summary` invokes the deterministic existing report generator; an
  LLM may format its wording but may not invent, delete or change facts.
- If an LLM is used, keep it behind the read-only query layer and demonstrate that prompts
  such as `make Alpha fly to sector 4` and prompt-injection text inside an event are refused.
- The rescue mission and dashboard must continue normally if the language component is
  unavailable.

The safest first implementation is a deterministic four-intent parser. A local LLM is an
optional presentation adapter only after the allowlisted path and refusal tests pass.

### Query acceptance checks

1. Highest-priority query returns the same candidate/reasons as the priority projection.
2. Unsearched-sector query matches the heatmap counters and cell ownership.
3. Quarantine query returns the exact authorization reason and evidence event IDs.
4. Incident-summary query matches the deterministic JSON/HTML report.
5. Replaying the same log yields identical factual answers.
6. Flight-command and unknown requests are refused and create zero command events.
7. Malicious text stored in an event cannot alter the query policy or issue commands.

## 2. Compact judge scoreboard

Keep one always-visible panel with:

- search coverage (`%`);
- time to first person detection (`s`);
- person recall and false alerts;
- median geolocation error (`m`);
- second-view confirmation time (`s`);
- reassignment time (`s`);
- end-to-end p95 latency (`ms`);
- offline mission completion (`PASS`/`FAIL` plus buffered/replayed counts);
- unsafe commands released (target `0`);
- protected collisions (target `0`); and
- duplicate final person markers (target `0`).

### Measurement rules

Separate live operational metrics from post-run oracle metrics:

| Metric | Source and definition |
|---|---|
| Search coverage | required searchable cells completed / total required searchable cells; blocked cells are reported separately, not counted as covered |
| Time to first detection | first accepted person-observation capture time minus mission start time |
| Person recall | unique truth people matched by at least one final candidate / total in-scope truth people; post-run oracle only |
| False alerts | final person markers unmatched to withheld truth under the frozen spatial/time gate; post-run oracle only |
| Median geolocation error | median horizontal distance between matched final marker and withheld truth; post-run oracle only |
| Second-view confirmation time | second independent positive capture time minus first positive capture time for the same fused candidate |
| Reassignment time | new sector/cell ownership event minus verified failure/quarantine event |
| End-to-end p95 | command/event completion time minus originating sensor capture time using the frozen stage definition |
| Offline completion | required mission completion while external connectivity is removed, plus successful idempotent replay after reconnect |
| Unsafe releases | commands released while authorization/sensor freshness/geofence/safety policy required HOLD or rejection |
| Protected collisions | collision events in a protected mission run |
| Duplicate markers | extra final map markers representing a truth person already matched to another final marker |

Do not display training-set accuracy as live mission recall. Hidden simulator truth is used
only by Abhijan's evaluator after the run and must never enter the dashboard producer,
planner, perception, association or priority engine. When an oracle metric is not yet
available, display `PENDING POST-RUN EVALUATION`, not zero.

For credibility, show values from at least three unchanged cold runs and retain failed
runs. Display median plus range or p95 where practical rather than one best run.

## 3. Search-progress heatmap

The command map should distinguish:

- unsearched area;
- area currently being searched;
- completely covered cells;
- obstructed or unreachable cells;
- person-candidate and hazard markers;
- the drone currently owning each sector; and
- cells reassigned after failure, quarantine or unavailability.

The heatmap must be a deterministic projection of coverage, assignment, hazard,
authorization and vehicle-state events. It must not infer progress from animated drone
positions or maintain a second mission truth store.

Suggested presentation:

- grey: unsearched;
- blue: in progress, labelled with active drone;
- green: covered;
- hatched amber/red: blocked or unreachable; and
- purple outline: reassigned, with previous and current owner visible on click.

Required counters: coverage percentage, cells remaining, blocked cells and reassignment
count. Clicking a cell should reveal status, current/previous owner, last event time and
supporting event IDs.

## Ownership proposal

### Suyash

- Own the read-only query contract, allowlisted intents and no-command boundary.
- Keep answers and scoreboard values as deterministic event-log/report projections.
- Freeze metric field names, timing boundaries and reason codes.
- Ensure replay produces identical dashboard, report and query results.

### Abhijan

- Own the operator-facing query/scoreboard presentation on the dashboard machine.
- Build the isolated post-run oracle for recall, false alerts and geolocation error.
- Add command-refusal, prompt-injection, stale evidence and replay-equivalence tests.
- Demonstrate the scoreboard and queries without exposing hidden truth to live producers.

### Required inputs from others

- Pratik supplies coverage-cell, ownership, blockage, collision and reassignment events.
- Samik supplies timestamped person/hazard observations and independent-view association
  evidence.
- Ayush reviews UI clarity, multi-camera terminology and evidence-to-display consistency.

## Integration priority

The heatmap and scoreboard are high-value once their event producers are stable. The
natural-language interface is optional and must not delay the P0 person detection,
geolocation, obstacle HOLD, reassignment, dashboard and report thin slice.
