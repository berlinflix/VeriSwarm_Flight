"""
Unit tests for Module 4 (CTI-SF): sliding-window isolation, the fail-safe
action mapping, and the active-set / quorum shrink.

Backs the claims in Section 3.6 / Algorithm 4:

    * a drone is isolated only when the fraction of its recent receipts the
      swarm rejected exceeds alpha (rho = rejected / W, divisor is W);
    * one unlucky rejection can never isolate an otherwise-honest drone, and
      old rejections age out of the window;
    * isolation shrinks the active peer set and recomputes the quorum;
    * the event is logged and announced exactly once;
    * isolation is a deterministic function of the public outcomes, so two
      nodes fed the same outcomes reach the same active set;
    * the fail-safe action follows the outcome (execute / fallback / defer).

    test_five_rejections_not_isolated          -> rho = 0.5 is not > 0.5
    test_sixth_rejection_isolates              -> rho = 0.6 crosses the threshold
    test_rho_divides_by_window                 -> divisor is W, not samples seen
    test_honest_originator_never_isolated      -> all-ACCEPTED window, no event
    test_single_rejection_never_isolates       -> one bad round is harmless
    test_old_rejections_age_out                -> window forgets, no isolation
    test_hooks_fire_exactly_once               -> log + announce called once
    test_record_after_isolation_is_noop        -> idempotent, no duplicate event
    test_roster_and_quorum_shrink              -> active set and k drop
    test_deterministic_across_nodes            -> same outcomes -> same active set
    test_no_quorum_not_counted_as_rejection    -> NO_QUORUM never isolates
    test_fallback_action_mapping               -> outcome -> SafeAction
    test_quorum_thresholds_match_engine        -> free function == engine
    test_window_must_be_positive               -> guard on W
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from protocol.peer_consensus import (  # noqa: E402
    ConsensusEngine,
    ConsensusOutcome,
    IsolationEvent,
    IsolationPolicy,
    IsolationTracker,
    SafeAction,
    fallback_action,
    quorum_thresholds,
)

import pytest  # noqa: E402

ACC = ConsensusOutcome.ACCEPTED
REJ = ConsensusOutcome.REJECTED
NQ = ConsensusOutcome.NO_QUORUM


def _tracker(n=5, W=10, alpha=0.5, hooks=False):
    roster = [f"d{i}" for i in range(n)]
    logged, announced = [], []
    kwargs = {}
    if hooks:
        kwargs = {"on_log": logged.append, "on_announce": announced.append}
    t = IsolationTracker(roster, policy=IsolationPolicy(window=W, alpha=alpha), **kwargs)
    return t, roster, logged, announced


# --------------------------------------------------------------------------- #
# Threshold boundary: rho = rejected / W
# --------------------------------------------------------------------------- #
def test_five_rejections_not_isolated():
    """5 rejections in W=10 give rho = 0.5, which is not strictly > 0.5."""
    t, roster, _, _ = _tracker(W=10, alpha=0.5)
    for r in range(5):
        assert t.record("d0", REJ, round_index=r + 1) is None
    assert not t.is_isolated("d0")
    assert t.rejection_rate("d0") == pytest.approx(0.5)


def test_sixth_rejection_isolates():
    """The 6th rejection makes rho = 0.6 > 0.5 and isolates the drone."""
    t, roster, _, _ = _tracker(W=10, alpha=0.5)
    event = None
    for r in range(6):
        event = t.record("d0", REJ, round_index=r + 1) or event
    assert t.is_isolated("d0")
    assert isinstance(event, IsolationEvent)
    assert event.round_index == 6
    assert event.rejected_in_window == 6
    assert event.rho == pytest.approx(0.6)


def test_rho_divides_by_window():
    """rho uses W as the divisor, not the number of samples seen so far:
    3 rejections early on are 3/10, never 3/3."""
    t, _, _, _ = _tracker(W=10, alpha=0.5)
    for r in range(3):
        t.record("d0", REJ, round_index=r + 1)
    assert t.rejection_rate("d0") == pytest.approx(0.3)
    assert not t.is_isolated("d0")


# --------------------------------------------------------------------------- #
# Honest drones are never falsely isolated
# --------------------------------------------------------------------------- #
def test_honest_originator_never_isolated():
    t, _, logged, announced = _tracker(W=10, alpha=0.5, hooks=True)
    for r in range(30):
        assert t.record("d1", ACC, round_index=r + 1) is None
    assert not t.is_isolated("d1")
    assert t.events == []
    assert logged == [] and announced == []


def test_single_rejection_never_isolates():
    """One bad round amid healthy ones must not remove a drone."""
    t, _, _, _ = _tracker(W=10, alpha=0.5)
    t.record("d1", REJ, round_index=1)
    for r in range(20):
        t.record("d1", ACC, round_index=r + 2)
    assert not t.is_isolated("d1")


def test_old_rejections_age_out():
    """Rejections older than the window fall out, so a recovered drone is safe.
    W=4: R,R,A,A,A,A -> the two R slide out; rho returns to 0, no isolation."""
    t, _, _, _ = _tracker(W=4, alpha=0.5)
    seq = [REJ, REJ, ACC, ACC, ACC, ACC]
    for r, o in enumerate(seq):
        assert t.record("d0", o, round_index=r + 1) is None
    assert not t.is_isolated("d0")
    assert t.rejection_rate("d0") == pytest.approx(0.0)


# --------------------------------------------------------------------------- #
# Event hooks: logged + announced exactly once
# --------------------------------------------------------------------------- #
def test_hooks_fire_exactly_once():
    t, _, logged, announced = _tracker(W=10, alpha=0.5, hooks=True)
    for r in range(10):
        t.record("d0", REJ, round_index=r + 1)
    assert len(logged) == 1
    assert len(announced) == 1
    assert logged[0].drone_id == "d0"
    assert logged[0] is announced[0] is t.events[0]


def test_record_after_isolation_is_noop():
    t, _, logged, announced = _tracker(W=10, alpha=0.5, hooks=True)
    for r in range(6):
        t.record("d0", REJ, round_index=r + 1)
    assert t.is_isolated("d0")
    # Further records for an isolated drone do nothing and emit no new events.
    for r in range(6, 12):
        assert t.record("d0", REJ, round_index=r + 1) is None
    assert len(t.events) == 1
    assert len(logged) == 1 and len(announced) == 1


# --------------------------------------------------------------------------- #
# Active set + quorum shrink
# --------------------------------------------------------------------------- #
def test_roster_and_quorum_shrink():
    t, roster, _, _ = _tracker(n=5, W=10, alpha=0.5)
    honest = "d1"
    assert len(t.active_roster) == 5
    assert t.active_voters(honest) == frozenset({"d0", "d2", "d3", "d4"})
    assert t.quorum_for(honest) == quorum_thresholds(4)

    for r in range(6):
        t.record("d0", REJ, round_index=r + 1)  # isolate d0
    assert t.is_isolated("d0")
    assert len(t.active_roster) == 4
    # d0 gone: the honest originator now has one fewer co-voter, quorum recomputed.
    assert t.active_voters(honest) == frozenset({"d2", "d3", "d4"})
    assert t.quorum_for(honest) == quorum_thresholds(3)
    event = t.events[0]
    assert len(event.roster_before) == 5 and len(event.roster_after) == 4
    assert "d0" not in event.roster_after


def test_deterministic_across_nodes():
    """Two nodes fed identical public outcomes reach the same active set with no
    shared state — the property that lets isolation need no trusted announce."""
    stream = [(f"d{i%5}", REJ if i % 5 == 0 else ACC) for i in range(60)]
    a, _, _, _ = _tracker(n=5, W=10, alpha=0.5)
    b, _, _, _ = _tracker(n=5, W=10, alpha=0.5)
    for i, (d, o) in enumerate(stream):
        a.record(d, o, round_index=i + 1)
        b.record(d, o, round_index=i + 1)
    assert a.active_roster == b.active_roster
    assert a.isolated == b.isolated == frozenset({"d0"})


def test_no_quorum_not_counted_as_rejection():
    t, _, _, _ = _tracker(W=10, alpha=0.5)
    for r in range(30):
        assert t.record("d0", NQ, round_index=r + 1) is None
    assert not t.is_isolated("d0")
    assert t.rejection_rate("d0") == pytest.approx(0.0)


# --------------------------------------------------------------------------- #
# Fail-safe action mapping + threshold helper
# --------------------------------------------------------------------------- #
def test_fallback_action_mapping():
    assert fallback_action(ACC) is SafeAction.EXECUTE
    assert fallback_action(REJ) is SafeAction.SAFE_FALLBACK
    assert fallback_action(NQ) is SafeAction.DEFER


def test_quorum_thresholds_match_engine():
    for k in (2, 3, 4, 6, 8, 10):
        eng = ConsensusEngine(num_peers=k)
        assert quorum_thresholds(k) == (eng.ack_threshold, eng.dispute_threshold)
        ack, rej = quorum_thresholds(k)
        assert ack + rej > k  # ACCEPT and REJECT can never both fire


def test_window_must_be_positive():
    with pytest.raises(ValueError):
        IsolationTracker(["d0"], policy=IsolationPolicy(window=0, alpha=0.5))
