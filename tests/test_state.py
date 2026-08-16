"""Unit tests for the stopwatch state machine (server.main.transition).

Runs with a fake clock so the 120s timeout logic is testable instantly.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

import main  # noqa: E402

# ---- fake clock (seconds) -------------------------------------------------
_fake_now = [10_000.0]


def fake_time():
    return _fake_now[0]


main.time.time = fake_time


def advance(seconds):
    _fake_now[0] += seconds


def fresh():
    return {
        "status": "stopped",
        "accumulated_ms": 0,
        "started_at_ms": None,
        "auto_resume": 0,
        "last_signal_ms": 0,
    }


def total(st):
    """Live total the API would report."""
    if st["status"] == "running" and st["started_at_ms"] is not None:
        return st["accumulated_ms"] + (main._now_ms() - st["started_at_ms"])
    return st["accumulated_ms"]


def test_start_and_running_total():
    st = fresh()
    main.transition(st, "start")
    assert st["status"] == "running"
    advance(5)
    assert total(st) == 5000
    main.transition(st, "heartbeat")
    advance(3)
    assert total(st) == 8000
    assert st["accumulated_ms"] == 0  # still an open segment


def test_inactive_pauses_then_alive_resumes():
    st = fresh()
    main.transition(st, "start")
    advance(10)
    main.transition(st, "inactive")
    assert st["status"] == "paused"
    assert st["auto_resume"] == 1
    assert st["accumulated_ms"] == 10_000

    advance(60)  # away for a minute
    assert total(st) == 10_000  # not counting while away

    main.transition(st, "alive")
    assert st["status"] == "running"
    advance(5)
    assert total(st) == 15_000


def test_explicit_pause_does_not_auto_resume():
    st = fresh()
    main.transition(st, "start")
    advance(10)
    main.transition(st, "pause")
    assert st["auto_resume"] == 0
    main.transition(st, "alive")  # still in the profile, but user paused
    assert st["status"] == "paused"
    assert total(st) == 10_000


def test_stale_gap_is_excluded():
    # Running, then the machine sleeps / browser is killed: no signals for
    # longer than the timeout. On the next signal the gap must NOT count.
    timeout_ms = int(main.SIGNAL_TIMEOUT_S * 1000)
    st = fresh()
    main.transition(st, "start")  # t=0
    advance(5)
    main.transition(st, "heartbeat")  # last signal at t=5
    advance(main.SIGNAL_TIMEOUT_S + 100)  # quiet for timeout + 100s

    main.transition(st, "alive")  # activity returns
    assert st["status"] == "running"
    assert st["accumulated_ms"] == 5_000 + timeout_ms  # counted until t=5+timeout
    advance(10)
    assert total(st) == 5_000 + timeout_ms + 10_000


def test_stale_gap_then_inactive_by_watchdog():
    # Same quiet period, but caught by the watchdog instead of a signal.
    timeout_ms = int(main.SIGNAL_TIMEOUT_S * 1000)
    st = fresh()
    main.transition(st, "start")
    advance(2)
    main.transition(st, "heartbeat")
    advance(main.SIGNAL_TIMEOUT_S + 200)
    main.transition(st, "timeout")  # watchdog path
    assert st["status"] == "paused"
    assert st["auto_resume"] == 1
    assert st["accumulated_ms"] == 2_000 + timeout_ms


def test_heartbeat_does_not_auto_resume():
    # The page stays "visible" even when covered by another app/profile, so a
    # page heartbeat must never resume a stopwatch paused by focus loss — only
    # the extension's "alive" signal may.
    st = fresh()
    main.transition(st, "start")
    advance(10)
    main.transition(st, "inactive")
    assert st["status"] == "paused"
    assert st["auto_resume"] == 1
    main.transition(st, "heartbeat")  # page still heartbeating while covered
    assert st["status"] == "paused"
    assert total(st) == 10_000
    main.transition(st, "alive")  # extension sees focus return
    assert st["status"] == "running"
    advance(3)
    assert total(st) == 13_000


def test_reset():
    st = fresh()
    main.transition(st, "start")
    advance(30)
    main.transition(st, "reset")
    assert st["status"] == "stopped"
    assert st["accumulated_ms"] == 0
    assert total(st) == 0
    main.transition(st, "alive")  # activity alone must not start it
    assert st["status"] == "stopped"
    main.transition(st, "start")
    assert st["status"] == "running"


def test_start_is_idempotent_when_running():
    st = fresh()
    main.transition(st, "start")
    advance(4)
    main.transition(st, "start")
    advance(2)
    assert total(st) == 6000
    assert st["accumulated_ms"] == 0


if __name__ == "__main__":
    tests = [
        (name, fn)
        for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    for name, fn in tests:
        _fake_now[0] = 10_000.0
        fn()
        print(f"PASS {name}")
    print(f"\n{len(tests)} tests passed")
