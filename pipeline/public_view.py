#!/usr/bin/env python3
"""Staff-facing projection of a per-staff JSON (v2, simplified page).

Only what the staff page needs is kept: logged hours by day, clock-on/off, leave, running total,
"being checked" state and the fortnight total. Overtime, the ordinary/overtime split, the 80h
target and the "paid from" payroll fields are removed here, so they never reach the browser or
the host. The team name is kept so the Worker can route a change request to the right manager.
"""
from __future__ import annotations

DAY_KEYS = ("date", "weekday", "day_label", "is_weekend", "in_range", "status",
            "on", "off", "on_exact", "off_exact", "worked_label", "running_total_label", "note",
            "counted_from", "counted_from_exact", "counted_from_source")


def _label(h: float) -> str:
    m = int(round((h or 0) * 60))
    return f"{m // 60}h {m % 60:02d}m"


def public_staff(s: dict | None) -> dict | None:
    if not s:
        return None
    t = s.get("totals", {})
    days = []
    for d in s.get("days", []):
        o = {k: d.get(k) for k in DAY_KEYS}
        o["leave"] = [{"type": l.get("type"), "label": l.get("label")}
                      for l in (d.get("leave") or []) if (l.get("hours") or 0) > 0]
        days.append(o)
    leave_h = round(t.get("leave_h") or 0, 2)
    return {
        "schema": 2,
        "name": s.get("name"),
        "first_name": s.get("first_name"),
        "team": s.get("team"),
        "period": s.get("period"),
        "range": s.get("range"),
        "updated_label": s.get("updated_label"),
        "totals": {
            "total_label": t.get("total_label") or _label(t.get("total_h")),
            "leave_h": leave_h,
            "leave_label": t.get("leave_label") or _label(leave_h),
            "being_checked_days": t.get("being_checked_days", 0),
            "being_checked_days_not_in_total": t.get("being_checked_days_not_in_total", 0),
            "fortnight_total_being_checked": bool(t.get("fortnight_total_being_checked")),
            "from_clock_times": t.get("hours_basis") in ("clock_estimate", "mixed"),
        },
        "days": days,
    }


FORBIDDEN = ("overtime", "ordinary", "paid_from", "target_h", "progress_pct", "ot_rule", "snap15", "staff_id")


def assert_clean(obj) -> None:
    """Raise if any payroll-only field name slipped into the staff-facing data."""
    def walk(o, path=""):
        if isinstance(o, dict):
            for k, v in o.items():
                if any(f in k.lower() for f in FORBIDDEN):
                    raise ValueError(f"staff-facing data contains payroll field {path}{k}")
                walk(v, f"{path}{k}.")
        elif isinstance(o, list):
            for v in o:
                walk(v, path)
    walk(obj)
