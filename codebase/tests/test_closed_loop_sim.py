from sim.closed_loop import ClosedLoopSimulation


def test_closed_loop_simulation_invariants():
    rows = ClosedLoopSimulation().run_all()
    assert len(rows) == 8
    assert sum(row.command_authorized for row in rows) == 2
