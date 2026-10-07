"""Unit tests for Jobman paid-start resolve_counted_from / estimate_hours."""
from datetime import time
from stv_pipeline import resolve_counted_from, estimate_hours, EARLY_CLOCK_NOTE_MIN, mins


def test_after_shift_from_clock():
    assert resolve_counted_from(time(7, 15), time(7, 0), None) == (time(7, 15), "clock")


def test_early_with_task_before_shift_uses_max():
    c, s = resolve_counted_from(time(6, 35), time(7, 0), time(6, 57))
    assert (c, s) == (time(6, 57), "first_task")
    assert mins(c) - mins(time(6, 35)) >= EARLY_CLOCK_NOTE_MIN


def test_early_task_straight_away_no_note():
    c, s = resolve_counted_from(time(6, 49), time(7, 0), time(6, 51))
    assert s == "first_task"
    assert mins(c) - mins(time(6, 49)) < EARLY_CLOCK_NOTE_MIN


def test_early_no_task_before_shift():
    assert resolve_counted_from(time(6, 35), time(7, 0), time(8, 5)) == (time(7, 0), "shift_start")
    assert resolve_counted_from(time(6, 35), time(7, 0), None) == (time(7, 0), "shift_start")


def test_no_paid_start():
    assert resolve_counted_from(time(6, 35), None, time(6, 40)) == (time(6, 35), "clock")


def test_estimate_early_task_from_max():
    fin = {"segments": [{"on": time(6, 35), "off": time(17, 7), "hours": 10.5333}]}
    rule = {"paid_start": "07:00", "break_h": 0.5, "break_after_h": 5.5}
    assert abs(estimate_hours(fin, rule, time(6, 57)) - 9.6667) < 1e-3
    # no early task: clip to 7:00
    assert abs(estimate_hours(fin, rule, time(8, 0)) - ((17 * 60 + 7 - 7 * 60) / 60 - 0.5)) < 1e-9


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
    print("all ok")
