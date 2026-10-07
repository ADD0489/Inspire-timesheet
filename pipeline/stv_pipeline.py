#!/usr/bin/env python3
"""Staff Timesheet View (STV) data pipeline, Inspire Kitchens.

VIEW ONLY. Jobman is read-only: this script only makes GET requests.

For one pay fortnight (Wed to Tue) and a date-to-date range inside it, builds a
staff-facing JSON per staff member: daily rows (date, weekday, clock on, clock off,
worked hours, leave, status ok / being checked, a friendly note) plus a fortnight-to-date
cumulative total (ordinary, overtime, leave, total) and a running total per day.

Sources, best first:
  1. Jobman activity log "Finalised timesheet entries ..." lines (GET /staff/{id}/activities).
     Used for the displayed clock-on / clock-off. Automatable every night.
  2. Jobman Custom Payroll CSV exports (browser only; optional --payroll-csv-dir).
     Used for daily paid Total, Ordinary Time and leave. Its clock columns are IGNORED
     (known Jobman bug: same on/off, blanks on paid days, ~30 min skew).
  3. Jobman saved Custom Payroll reports (GET /reports/{id}, optional --report-totals).
     Period-level totals only, and only for whatever date range is saved on the report
     (GET query params are ignored; changing the range is a write, which we never do).
     Used as a fortnight-level cross-check when the saved range matches.
  When no payroll CSV covers a day (the normal nightly case), worked hours are an
  ESTIMATE from the Finalised clocks using per-staff rules in staff_rules.json
  (paid start time, unpaid break), calibrated against a fortnight with CSV totals.

A day is "being checked" when the sources disagree by 15 minutes or more on the day's
hours, when paid hours exist with no clocks (or the reverse), or when the clocks look
broken (a >16h segment dropped, a gap between segments, a span over 14h). Being-checked
days show no clock times and no hours to staff and are not counted in the displayed total
(the page says how many days are being checked).

Display choice: EXACT clock times (to the minute), not snapped. Reason: it is what staff
saw on the kiosk, so they can recognise it; snapping moves times by up to 7 minutes and
would prompt "that is not when I clocked on" questions; and the snapped 15-minute value is
a convention of the manager approval sheet, not a pay rule (hours come from Jobman totals,
not from the displayed clocks). The snapped values are still in the JSON (on_snap15 /
off_snap15) so the page can switch with one setting (DISPLAY_SNAP in Code.gs).

Usage:
  python3 stv_pipeline.py --period-start 2026-09-23 --to 2026-10-06 \
      --payroll-csv-dir /workspace/timesheet-template/exports-23sep-6oct/payroll-csv --report-totals
  python3 stv_pipeline.py            # current fortnight, from its Wednesday to today
  python3 stv_pipeline.py --calibrate-rules ...   # (re)fit staff_rules.json from CSV totals
No em dashes in any output text.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time as time_mod
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
TEMPLATE_DIR = Path("/workspace/timesheet-template")
sys.path.insert(0, str(TEMPLATE_DIR))
from build_timesheet_v4 import parse_clock, snap_to_15  # noqa: E402  (shared with approval sheet)

NZ = ZoneInfo("Pacific/Auckland")
ORG_ID = "fbc41182-dffc-4597-ab02-462388d6a239"
API = f"https://api.jobmanapp.com/api/v1/organisations/{ORG_ID}"
FORTNIGHT_ANCHOR = date(2026, 9, 23)  # a pay-fortnight Wednesday
STAFF_MAP_PATH = Path("/workspace/staff_tab_map.json")
RULES_PATH = HERE / "staff_rules.json"
STAFF_IDS_PATH = HERE / "staff_ids.json"
REPORT_NAMES = ["AKL Operations", "AKL Sales", "QT Sales + Dunc", "QT Ops"]
TAB_ORDER = REPORT_NAMES
ABSURD_HOURS = 16.0
LONG_SPAN_HOURS = 14.0
# Jobman auto clock-off (about 3h after End Shift) records the clock-off AT End Shift, so a
# Finalised clock-off of exactly 6:00pm usually means a missed clock-off (Anna, Andrea, Clebson
# 23 Sep to 6 Oct 2026). Add other End Shift times here if rosters differ.
AUTO_OFF_TIMES = {"18:00"}
CHECK_THRESHOLD_H = 0.25  # 15 minutes
EARLY_CLOCK_NOTE_MIN = 10  # note when clock-on is this many minutes or more before counted time starts
ORDINARY_DAY_H = 8.0  # Jobman Ordinary Time caps at 8h a day in our payroll exports
TARGET_HOURS = 80.0
SCHEMA = 1
LEAVE_COLUMNS = ["Annual Leave", "Bereavement Leave", "Sick Leave", "Unpaid Leave"]
LEAVE_LABEL = {
    "Annual Leave": "Annual leave",
    "Bereavement Leave": "Bereavement leave",
    "Sick Leave": "Sick leave",
    "Unpaid Leave": "Unpaid leave",
}
FINALISED_RE = re.compile(
    r"Finalised timesheet entries for attendance on (\d{2}/\d{2}/\d{4}) from (.+?) to (.+?)\.",
    re.IGNORECASE,
)

# ----------------------------------------------------------------------------- helpers

def fortnight_for(d: date) -> tuple[date, date]:
    k = (d - FORTNIGHT_ANCHOR).days // 14
    start = FORTNIGHT_ANCHOR + timedelta(days=14 * k)
    return start, start + timedelta(days=13)


def daterange(a: date, b: date):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def mins(t: time) -> int:
    return t.hour * 60 + t.minute


def seg_hours(on: time, off: time) -> float:
    a, b = mins(on), mins(off)
    return (b - a) / 60.0 if b >= a else (24 * 60 - a + b) / 60.0


def fmt_clock(t: time | None) -> str | None:
    """7:05am style (NZ friendly)."""
    if t is None:
        return None
    h = t.hour % 12 or 12
    return f"{h}:{t.minute:02d}{'am' if t.hour < 12 else 'pm'}"


def fmt_hm(h: float | None) -> str:
    if h is None:
        return ""
    total = int(round(h * 60))
    sign = "-" if total < 0 else ""
    hh, mm = divmod(abs(total), 60)
    return f"{sign}{hh}h {mm:02d}m"


def fmt_date_long(d: date) -> str:
    return f"{d.day} {d.strftime('%b')} {d.year}"


def period_label(a: date, b: date) -> str:
    if a.year == b.year:
        return f"{a.day} {a.strftime('%b')} to {b.day} {b.strftime('%b')} {b.year}"
    return f"{fmt_date_long(a)} to {fmt_date_long(b)}"


def now_nz() -> datetime:
    return datetime.now(tz=NZ)


def updated_label(dt: datetime) -> str:
    h = dt.hour % 12 or 12
    return f"{dt.strftime('%a')} {dt.day} {dt.strftime('%b')} {dt.year}, {h}:{dt.minute:02d}{'am' if dt.hour < 12 else 'pm'}"


def r2(x: float) -> float:
    return round(float(x) + 0.0, 4)


# ----------------------------------------------------------------------------- Jobman (GET only)

class Jobman:
    """Read-only Jobman client with a simple rate limiter (<= ~45 requests/min; limit is 50)."""

    MIN_INTERVAL_S = 1.35

    def __init__(self):
        tok = os.environ.get("JOBMAN_API_TOKEN")
        if not tok:
            raise SystemExit("JOBMAN_API_TOKEN not set")
        self.h = {"Authorization": f"Bearer {tok}", "Accept": "application/json"}
        self.s = requests.Session()
        self.last = 0.0
        self.calls = 0

    def get(self, url: str, params: dict | None = None) -> dict:
        for attempt in range(8):
            wait = self.MIN_INTERVAL_S - (time_mod.monotonic() - self.last)
            if wait > 0:
                time_mod.sleep(wait)
            self.last = time_mod.monotonic()
            self.calls += 1
            r = self.s.get(url, params=params, headers=self.h, timeout=60)
            if r.status_code == 429:
                back = min(65, 5 * (attempt + 1))
                print(f"  429 rate limited; sleeping {back}s", flush=True)
                time_mod.sleep(back)
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"Too many 429s for {url}")


def load_staff_ids(staff_names: list[str], jm: Jobman | None) -> dict[str, str]:
    ids = json.loads(STAFF_IDS_PATH.read_text()) if STAFF_IDS_PATH.exists() else {}
    missing = [n for n in staff_names if n not in ids]
    if missing and jm:
        page, last = 1, 1
        found: dict[str, str] = {}
        while page <= last:
            payload = jm.get(f"{API}/staff", {"page": page})
            blk = payload.get("staff") or payload
            data = blk.get("data") if isinstance(blk, dict) else blk
            meta = (blk.get("meta") if isinstance(blk, dict) else {}) or {}
            last = int(meta.get("last_page") or page)
            for row in data or []:
                if row.get("deactivated_at") or row.get("deleted_at"):
                    continue
                found.setdefault(row.get("name"), row["id"])
            page += 1
        for n in missing:
            if n in found:
                ids[n] = found[n]
        STAFF_IDS_PATH.write_text(json.dumps(ids, indent=2))
    still = [n for n in staff_names if n not in ids]
    if still:
        raise SystemExit(f"No Jobman staff id for: {still}")
    return ids


def pull_activities(jm: Jobman, staff_id: str, since: date, cache_dir: Path, max_age_min: int) -> list[dict]:
    """Newest-first pages; stop once a page is entirely older than `since` minus 3 days."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cp = cache_dir / f"{staff_id}.json"
    if cp.exists():
        c = json.loads(cp.read_text())
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(c["pulled_at_utc"])).total_seconds() / 60
        if age <= max_age_min and date.fromisoformat(c["since"]) <= since:
            return c["activities"]
    cutoff = datetime.combine(since - timedelta(days=3), time(0), tzinfo=timezone.utc)
    out: list[dict] = []
    page, last = 1, 1
    while page <= last and page <= 40:
        blk = jm.get(f"{API}/staff/{staff_id}/activities", {"page": page})["activities"]
        rows = blk.get("data") or []
        out.extend(rows)
        last = int((blk.get("meta") or {}).get("last_page") or 1)
        stamps = [datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")) for r in rows if r.get("created_at")]
        if stamps and max(stamps) < cutoff:
            break
        page += 1
    cp.write_text(json.dumps({"staff_id": staff_id, "since": since.isoformat(),
                              "pulled_at_utc": datetime.now(timezone.utc).isoformat(),
                              "pages": page, "activities": out}, default=str))
    return out


def pull_task_entries(jm: Jobman, staff_id: str, since: date, cache_dir: Path, max_age_min: int) -> list[dict]:
    """Task timesheet entries (GET /staff/{id}/timesheet, newest first, 15 per page).

    Used by the Jobman paid-start rule: if someone clocks on before shift start and starts a task
    before shift start, early hours are paid; if they clock on early but do not start a task before
    shift start, hours count from shift start. Cached per staff; a refresh only pages back until it
    overlaps entries already cached (minus 2 days, for late edits) or reaches `since`.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    cp = cache_dir / f"{staff_id}.json"
    cached: dict[str, dict] = {}
    c_since = None
    if cp.exists():
        c = json.loads(cp.read_text())
        cached = {e["id"]: e for e in c.get("entries", [])}
        c_since = date.fromisoformat(c["since"])
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(c["pulled_at_utc"])).total_seconds() / 60
        if age <= max_age_min and c_since <= since:
            return list(cached.values())
    since_utc = datetime.combine(since - timedelta(days=1), time(0), tzinfo=timezone.utc)
    stop_at = since_utc
    if cached and c_since and c_since <= since:
        newest = max(datetime.fromisoformat(e["started_at"].replace("Z", "+00:00")) for e in cached.values())
        stop_at = max(since_utc, newest - timedelta(days=2))
    page, last, fresh = 1, 1, {}
    while page <= last and page <= 60:
        blk = jm.get(f"{API}/staff/{staff_id}/timesheet", {"page": page})["timesheet_entries"]
        rows = blk.get("data") or []
        for r in rows:
            fresh[r["id"]] = r
        last = int((blk.get("meta") or {}).get("last_page") or 1)
        stamps = [datetime.fromisoformat(r["started_at"].replace("Z", "+00:00")) for r in rows if r.get("started_at")]
        if not stamps or max(stamps) < stop_at:
            break
        page += 1
    merged = {**cached, **fresh}
    keep_since = min(since, c_since) if (cached and c_since) else since
    keep_utc = datetime.combine(keep_since - timedelta(days=1), time(0), tzinfo=timezone.utc)
    keep = [e for e in merged.values()
            if e.get("started_at") and datetime.fromisoformat(e["started_at"].replace("Z", "+00:00")) >= keep_utc]
    cp.write_text(json.dumps({"staff_id": staff_id, "since": keep_since.isoformat(),
                              "pulled_at_utc": datetime.now(timezone.utc).isoformat(), "pages": page,
                              "entries": keep}, default=str))
    return keep


def first_task_starts(entries: list[dict], a: date, b: date) -> dict[date, time]:
    """Earliest task start per NZ calendar day (to the minute, seconds dropped)."""
    out: dict[date, time] = {}
    for e in entries:
        if not e.get("started_at"):
            continue
        local = datetime.fromisoformat(e["started_at"].replace("Z", "+00:00")).astimezone(NZ)
        d = local.date()
        if d < a or d > b:
            continue
        t = time(local.hour, local.minute)
        if d not in out or mins(t) < mins(out[d]):
            out[d] = t
    return out


def finalised_days(activities: list[dict], a: date, b: date) -> dict[date, dict]:
    by: dict[date, list] = {}
    for act in activities:
        m = FINALISED_RE.search(act.get("event") or "")
        if not m:
            continue
        d = datetime.strptime(m.group(1), "%d/%m/%Y").date()
        if d < a or d > b:
            continue
        on, off = parse_clock(m.group(2).strip()), parse_clock(m.group(3).strip())
        if on is None or off is None:
            continue
        by.setdefault(d, []).append({"on": on, "off": off, "hours": seg_hours(on, off),
                                     "created_at": act.get("created_at")})
    out = {}
    for d, segs in by.items():
        sensible = [s for s in segs if s["hours"] <= ABSURD_HOURS]
        dropped = [s for s in segs if s["hours"] > ABSURD_HOURS]
        if not sensible:
            out[d] = {"mode": "all_absurd", "on": None, "off": None, "sum_h": 0.0, "span_h": 0.0,
                      "segments": sensible, "dropped": dropped}
            continue
        on = min((s["on"] for s in sensible), key=mins)
        off = max((s["off"] for s in sensible), key=mins)
        sum_h = sum(s["hours"] for s in sensible)
        span_h = seg_hours(on, off)
        mode = "single" if len(sensible) == 1 else ("multi_contiguous" if abs(span_h - sum_h) <= 0.1 else "multi_gap")
        out[d] = {"mode": mode, "on": on, "off": off, "sum_h": sum_h, "span_h": span_h,
                  "segments": sensible, "dropped": dropped}
    return out


def fetch_report_totals(jm: Jobman) -> dict:
    """{staff_id: {...period totals...}, '_ranges': {report: (from,to)}} from saved reports (GET only)."""
    lst = jm.get(f"{API}/reports")
    items = lst.get("reports", lst) if isinstance(lst, dict) else lst
    items = items.get("data", items) if isinstance(items, dict) else items
    by_name = {}
    for it in items or []:
        if it.get("name") in REPORT_NAMES:
            prev = by_name.get(it["name"])
            if not prev or (it.get("updated_at") or "") > (prev.get("updated_at") or ""):
                by_name[it["name"]] = it
    out: dict = {"_ranges": {}}
    for name, it in by_name.items():
        d = jm.get(f"{API}/reports/{it['id']}")
        rd = d.get("report_data") or {}
        f = rd.get("filters") or {}
        rng = (f.get("date_range_from"), f.get("date_range_to"))
        out["_ranges"][name] = {"from": rng[0], "to": rng[1], "date_range": f.get("date_range")}
        for sa in (rd.get("data") or {}).get("staff_attendances") or []:
            t = sa.get("totals") or {}
            pay = (t.get("pay_items") or {})
            ordinary = sum(p.get("time") or 0 for p in pay.get("earnings") or [] if p.get("name") == "Ordinary Time")
            leave = {p.get("name"): (p.get("time") or 0) / 3600 for p in pay.get("leave") or []}
            out[sa["id"]] = {"report": name, "from": rng[0], "to": rng[1],
                             "total_h": (t.get("total_time") or 0) / 3600,
                             "ordinary_h": ordinary / 3600,
                             "leave_h": (t.get("total_leave_time") or 0) / 3600,
                             "leave_by_type_h": leave}
    return out


# ----------------------------------------------------------------------------- payroll CSV (browser export)

def load_payroll_csv(csv_dir: Path | None) -> dict[tuple[str, date], dict]:
    out: dict[tuple[str, date], dict] = {}
    if not csv_dir:
        return out
    for p in sorted(Path(csv_dir).glob("*.csv")):
        with p.open(newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                r = {(k or "").strip(): (v or "").strip() for k, v in r.items() if k}
                name = r.get("Staff")
                if not name or not r.get("Date"):
                    continue
                d = datetime.strptime(r["Date"], "%d/%m/%Y").date()

                def n(k):
                    try:
                        return float(r.get(k) or 0)
                    except ValueError:
                        return 0.0
                leave = {k: n(k) for k in r if k.endswith("Leave") and n(k) > 0}
                out[(name, d)] = {"total_h": n("Total"), "ordinary_h": n("Ordinary Time"), "leave": leave,
                                  "csv_on": r.get("Clocked-on") or "", "csv_off": r.get("Clocked-off") or "",
                                  "file": p.name}
    return out


# ----------------------------------------------------------------------------- estimate rules

DEFAULT_RULE = {"paid_start": None, "break_h": 0.5, "break_after_h": 5.5}


def estimate_hours(fin: dict, rule: dict, first_task: time | None = None) -> float:
    """Paid-hours estimate from Finalised segments using Jobman paid-start rules.

    If clock-on is before paid_start (shift start) and there is no first_task before paid_start,
    clip clock-on up to paid_start. If first_task is before paid_start, early time counts from
    max(clock-on, first_task) (no clip to shift start). Then subtract the unpaid break when the
    day is long enough.
    """
    ps = parse_clock(rule.get("paid_start")) if rule.get("paid_start") else None
    early_task_paid = bool(ps and first_task and mins(first_task) < mins(ps))
    total = 0.0
    for s in fin["segments"]:
        on, off = s["on"], s["off"]
        if mins(off) < mins(on):  # overnight; leave as-is
            total += s["hours"]
            continue
        if early_task_paid and mins(on) < mins(ps):
            # paid from later of clock-on and first task (do not clip to shift start)
            start = on if mins(on) >= mins(first_task) else first_task
            on = start if mins(start) < mins(off) else off
        elif ps and mins(on) < mins(ps):
            on = ps if mins(ps) < mins(off) else off
        total += (mins(off) - mins(on)) / 60.0
    if total > float(rule.get("break_after_h", 5.5)):
        total -= float(rule.get("break_h", 0.0))
    return max(0.0, total)


def calibrate(staff_names, fin_by_staff, csv_rows, task_by_staff=None) -> dict:
    task_by_staff = task_by_staff or {}
    starts = [None, "06:30", "07:00", "07:30", "08:00", "08:30", "09:00"]
    breaks = [0.0, 0.5]
    afters = [4.0, 5.0, 5.5, 6.0]
    rules = {}
    for name in staff_names:
        pairs = []
        ft_map = task_by_staff.get(name) or {}
        for d, fin in fin_by_staff.get(name, {}).items():
            c = csv_rows.get((name, d))
            if not c or fin["mode"] not in ("single", "multi_contiguous"):
                continue
            worked = c["total_h"] - sum(c["leave"].values())
            if worked <= 0:
                continue
            pairs.append((fin, worked, ft_map.get(d)))
        best = None
        for ps in starts:
            for b in breaks:
                for af in afters:
                    rule = {"paid_start": ps, "break_h": b, "break_after_h": af}
                    errs = [abs(estimate_hours(f, rule, ft) - w) for f, w, ft in pairs]
                    ok = sum(1 for e in errs if e < CHECK_THRESHOLD_H)
                    mae = sum(errs) / len(errs) if errs else 0
                    simplicity = (ps is not None) + (af != 5.5)
                    key = (-ok, round(mae, 3), simplicity)
                    if best is None or key < best[0]:
                        best = (key, rule, ok, len(pairs), mae)
        if best and best[3]:
            r = dict(best[1])
            r.update({"fit_days_within_15m": best[2], "fit_days": best[3], "fit_mean_abs_err_min": round(best[4] * 60, 1)})
            rules[name] = r
        else:
            rules[name] = dict(DEFAULT_RULE, fit_days=0, note="no CSV days to fit; default rule")
    return rules


# ----------------------------------------------------------------------------- build


def resolve_counted_from(on: time | None, paid_start: time | None, first_task: time | None):
    """Jobman paid-start rule (Aaron, confirmed 7 Oct 2026 NZ).

    Returns (counted_time|None, source|None). source: "clock" | "shift_start" | "first_task".

    1. Start at/after shift start -> hours from clock-on.
    2. Clock before shift AND start a task before shift -> early hours paid (counted from clock /
       later of clock and first_task); no early note.
    3. Clock before shift AND no task before shift -> hours from shift start; show early note.
    """
    if not on:
        return None, None
    if not paid_start:
        return on, "clock"
    if mins(on) >= mins(paid_start):
        return on, "clock"
    if first_task and mins(first_task) < mins(paid_start):
        # Early hours paid from the later of clock-on and first task (usually clock-on if they
        # started a task straight away). Gap >= EARLY_CLOCK_NOTE_MIN shows a first_task note.
        counted = on if mins(on) >= mins(first_task) else first_task
        return counted, "first_task"
    return paid_start, "shift_start"


FRIENDLY = {
    "absurd_segment": "Jobman has an unusual clock entry for this day.",
    "multi_gap": "This day has more than one clock entry with a gap between them.",
    "long_span": "The clock times for this day look longer than usual.",
    "zero_length": "The clock-on and clock-off times are the same.",
    "paid_no_clocks": "Hours are recorded but the clock times are missing.",
    "clocked_not_paid": "Clock times are recorded but no hours are showing yet.",
    "total_mismatch": "Two Jobman records for this day do not agree.",
    "auto_clock_off": "This looks like an automatic clock-off, so your finish time is being confirmed.",
    "report_period_mismatch": "The fortnight total is being double-checked against payroll.",
}


MAX_SANE_DAY_H = 14.0


def build_staff(name, staff_id, team, period, rng, fin, csv_rows, csv_cover, rule, report_tot, generated,
                first_task=None, counted_pref="jobman"):
    """Pass 1: work out each day from the sources. Pass 2: decide what staff see."""
    first_task = first_task or {}
    p_start, p_end = period
    r_from, r_to = rng
    raw = []
    for d in daterange(p_start, p_end):
        rec = {"d": d, "in_range": r_from <= d <= r_to}
        if not rec["in_range"]:
            raw.append(rec)
            continue
        f = fin.get(d)
        c = csv_rows.get((name, d))
        have_csv = csv_cover(d)
        reasons = []
        if f:
            if f["dropped"] or f["mode"] == "all_absurd":
                reasons.append("absurd_segment")
            if f["mode"] == "multi_gap":
                reasons.append("multi_gap")
            if f["span_h"] > LONG_SPAN_HOURS:
                reasons.append("long_span")
            if f["on"] is not None and f["on"] == f["off"]:
                reasons.append("zero_length")
            if f["off"] is not None and f["off"].strftime("%H:%M") in AUTO_OFF_TIMES:
                reasons.append("auto_clock_off")
        ft_day = first_task.get(d)
        est = estimate_hours(f, rule, ft_day) if f and f["on"] is not None else None
        leave, worked, src, ordinary, ot = {}, None, "none", 0.0, 0.0
        if have_csv:
            c = c or {"total_h": 0.0, "ordinary_h": 0.0, "leave": {}}
            leave = c["leave"]
            lh = sum(leave.values())
            worked = max(0.0, c["total_h"] - lh)
            ordinary = min(c["ordinary_h"], worked) if worked else 0.0
            ot = max(0.0, c["total_h"] - c["ordinary_h"] - lh)
            src = "payroll" if (worked or lh) else "none"
            if worked > 0 and not f:
                reasons.append("paid_no_clocks")
            if f and f["on"] is not None and worked == 0 and lh == 0 and (est or 0) >= CHECK_THRESHOLD_H:
                reasons.append("clocked_not_paid")
            if f and est is not None and worked > 0 and abs(est - worked) >= CHECK_THRESHOLD_H:
                reasons.append("total_mismatch")
        elif est is not None:
            worked = est
            ordinary = min(ORDINARY_DAY_H, worked)
            ot = max(0.0, worked - ORDINARY_DAY_H)
            src = "clock_estimate"
        rec.update({"f": f, "c": c if have_csv else None, "have_csv": have_csv, "reasons": list(dict.fromkeys(reasons)),
                    "est": est, "leave": leave, "leave_h": sum(leave.values()), "worked": worked, "src": src,
                    "ordinary": ordinary, "ot": ot})
        raw.append(rec)

    all_hours_incl_checked = sum((r.get("worked") or 0) + r.get("leave_h", 0) for r in raw if r["in_range"])
    period_check = None
    if report_tot and report_tot.get("from") == p_start.isoformat() and report_tot.get("to") == r_to.isoformat():
        diff = all_hours_incl_checked - report_tot["total_h"]
        period_check = {"report_total_h": r2(report_tot["total_h"]), "pipeline_total_incl_checked_h": r2(all_hours_incl_checked),
                        "diff_h": r2(diff), "status": "being_checked" if abs(diff) >= CHECK_THRESHOLD_H else "ok"}
    elif report_tot:
        period_check = {"status": "not_compared",
                        "reason": f"saved report range {report_tot.get('from')} to {report_tot.get('to')} does not match {p_start} to {r_to}"}
    period_ok = not (period_check and period_check.get("status") == "being_checked")

    days_out, internal = [], []
    running = 0.0
    tot = {"worked_h": 0.0, "ordinary_h": 0.0, "overtime_h": 0.0, "leave_h": 0.0}
    leave_by_type: dict[str, float] = {}
    n_checked = n_checked_counted = n_est = n_pay = 0
    for rec in raw:
        d = rec["d"]
        row = {"date": d.isoformat(), "weekday": d.strftime("%A"), "weekday_short": d.strftime("%a"),
               "day_label": f"{d.strftime('%a')} {d.day} {d.strftime('%b')}", "is_weekend": d.weekday() >= 5,
               "in_range": rec["in_range"]}
        if not rec["in_range"]:
            row.update({"status": "future" if d > r_to else "not_loaded", "on": None, "off": None,
                        "worked_h": None, "worked_label": "", "hours_source": None, "leave": [], "leave_h": 0.0,
                        "running_total_h": None, "running_total_label": "", "note": ""})
            days_out.append(row)
            continue
        f, worked, leave, lh, src, reasons = rec["f"], rec["worked"], rec["leave"], rec["leave_h"], rec["src"], rec["reasons"]
        if reasons:
            status = "being_checked"
        elif (worked or 0) > 0 or lh > 0 or f:
            status = "ok"
        elif d == generated.date():
            status = "today_pending"
        else:
            status = "no_entry"
        show_clocks = status == "ok"
        # Hours on a being-checked day are shown (and counted) only when they come from the payroll
        # totals, look sane, and the fortnight total agrees with Jobman's saved report.
        show_hours = status == "ok" or (src == "payroll" and period_ok and 0 < (worked or 0) + lh <= MAX_SANE_DAY_H)
        if status == "being_checked":
            n_checked += 1
            if show_hours:
                n_checked_counted += 1
        if show_hours:
            running += (worked or 0) + lh
            tot["worked_h"] += worked or 0
            tot["ordinary_h"] += rec["ordinary"]
            tot["overtime_h"] += rec["ot"]
            tot["leave_h"] += lh
            for k, v in leave.items():
                leave_by_type[k] = leave_by_type.get(k, 0) + v
            if src == "clock_estimate":
                n_est += 1
            elif src == "payroll":
                n_pay += 1
        clock_on = f["on"] if f else None
        clock_off = f["off"] if f else None
        on = clock_on if show_clocks else None
        off = clock_off if show_clocks else None
        note = ""
        if status == "being_checked":
            note = FRIENDLY.get(reasons[0], "")
            if not show_hours:
                note += " Not in your total yet."
        elif lh and not worked:
            note = ", ".join(LEAVE_LABEL.get(k, k) for k in leave)
        elif status == "today_pending":
            note = "Today's hours show here after you clock off."
        ps = parse_clock(rule.get("paid_start")) if rule.get("paid_start") else None
        paid_from = fmt_clock(ps) if (clock_on and ps and mins(clock_on) < mins(ps)) else None
        # When did counted time start (for the "Clocked on 6:35am; hours count from 7:00am" note)?
        # Default: Jobman paid-start rule (Aaron, 7 Oct 2026 NZ). Legacy --counted-from start_rule /
        # first_task still available. No rule = counted from clock-on, no note.
        # Use clock_on (not display on) so internal_qa still records the rule on being_checked days.
        ft = first_task.get(d)
        counted, counted_src = None, None
        if clock_on and counted_pref == "jobman":
            counted, counted_src = resolve_counted_from(clock_on, ps, ft)
        elif clock_on and counted_pref == "first_task" and ft:
            counted, counted_src = (ft if mins(ft) > mins(clock_on) else clock_on), "first_task"
        elif clock_on and ps:
            counted, counted_src = (ps if mins(ps) > mins(clock_on) else clock_on), "start_rule"
        early_min = (mins(counted) - mins(clock_on)) if (counted and clock_on) else 0
        # Staff-facing early note only on ok days with visible clocks
        show_early = bool(show_clocks and counted and early_min >= EARLY_CLOCK_NOTE_MIN)
        row.update({
            "status": status,
            "on": fmt_clock(on), "off": fmt_clock(off),
            "paid_from": paid_from,
            "counted_from": fmt_clock(counted) if show_early else None,
            "counted_from_exact": counted.strftime("%H:%M") if show_early else None,
            "counted_from_source": counted_src if show_early else None,
            "clocked_on_early_min": early_min if show_early else 0,
            "on_exact": on.strftime("%H:%M") if on else None,
            "off_exact": off.strftime("%H:%M") if off else None,
            "on_snap15": fmt_clock(snap_to_15(on)) if on else None,
            "off_snap15": fmt_clock(snap_to_15(off)) if off else None,
            "worked_h": r2(worked) if (show_hours and worked is not None) else None,
            "worked_label": fmt_hm(worked) if (show_hours and worked) else "",
            "hours_source": src if show_hours else None,
            "leave": [{"type": LEAVE_LABEL.get(k, k), "hours": r2(v), "label": fmt_hm(v)} for k, v in leave.items()] if show_hours else [],
            "leave_h": r2(lh) if show_hours else 0.0,
            "running_total_h": r2(running),
            "running_total_label": fmt_hm(running),
            "note": note.strip(),
        })
        days_out.append(row)
        c = rec["c"]
        internal.append({"staff": name, "date": d.isoformat(), "status": status, "reasons": reasons,
                         "shown_hours": show_hours, "shown_clocks": show_clocks,
                         "fin_mode": f["mode"] if f else None,
                         "fin_on": f["on"].strftime("%H:%M") if f and f["on"] else None,
                         "fin_off": f["off"].strftime("%H:%M") if f and f["off"] else None,
                         "fin_sum_h": r2(f["sum_h"]) if f else None,
                         "fin_dropped": [f"{s['on'].strftime('%H:%M')}-{s['off'].strftime('%H:%M')}" for s in f["dropped"]] if f else [],
                         "estimate_h": r2(rec["est"]) if rec["est"] is not None else None,
                         "csv_available": rec["have_csv"],
                         "csv_total_h": c["total_h"] if c else None,
                         "csv_ordinary_h": c["ordinary_h"] if c else None,
                         "csv_clocks": f"{c.get('csv_on','')}-{c.get('csv_off','')}" if c else None,
                         "worked_h_best": r2(worked) if worked is not None else None,
                         "overtime_h": r2(rec["ot"]), "leave": leave, "hours_source": src,
                         "paid_start": rule.get("paid_start"),
                         "first_task_start": ft.strftime("%H:%M") if ft else None,
                         "counted_from": counted.strftime("%H:%M") if counted else None,
                         "counted_from_source": counted_src,
                         "on_snap15": snap_to_15(f["on"]).strftime("%H:%M") if f and f["on"] else None,
                         "off_snap15": snap_to_15(f["off"]).strftime("%H:%M") if f and f["off"] else None})
    total_h = tot["worked_h"] + tot["leave_h"]
    basis = ("payroll" if n_pay and not n_est else "clock_estimate" if n_est and not n_pay
             else "mixed" if n_est and n_pay else "none")
    totals = {
        "worked_h": r2(tot["worked_h"]), "ordinary_h": r2(tot["ordinary_h"]),
        "overtime_h": r2(tot["overtime_h"]), "leave_h": r2(tot["leave_h"]),
        "leave_by_type": {LEAVE_LABEL.get(k, k): r2(v) for k, v in leave_by_type.items()},
        "total_h": r2(total_h),
        "total_label": fmt_hm(total_h), "worked_label": fmt_hm(tot["worked_h"]), "ordinary_label": fmt_hm(tot["ordinary_h"]),
        "overtime_label": fmt_hm(tot["overtime_h"]), "leave_label": fmt_hm(tot["leave_h"]),
        "target_h": TARGET_HOURS, "progress_pct": round(min(100.0, 100 * total_h / TARGET_HOURS), 1),
        "being_checked_days": n_checked,
        "being_checked_days_in_total": n_checked_counted,
        "being_checked_days_not_in_total": n_checked - n_checked_counted,
        "fortnight_total_being_checked": not period_ok,
        "hours_basis": basis,
        "ot_rule": "Overtime here means hours over 8 in a day, the same rule as the approval sheet.",
    }
    staff_json = {
        "schema": SCHEMA,
        "staff_id": staff_id, "name": name, "first_name": name.split()[0], "team": team,
        "period": {"start": p_start.isoformat(), "end": p_end.isoformat(), "label": period_label(p_start, p_end)},
        "range": {"from": r_from.isoformat(), "to": r_to.isoformat(), "label": period_label(r_from, r_to)},
        "updated_at_nz": generated.isoformat(timespec="minutes"),
        "updated_label": updated_label(generated),
        "days": days_out,
        "totals": totals,
    }
    internal.append({"staff": name, "date": "PERIOD", "period_check": period_check, "status": "period"})
    return staff_json, internal


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--period-start", help="Fortnight Wednesday YYYY-MM-DD (default: fortnight containing --to)")
    ap.add_argument("--from", dest="date_from", help="Range start (default: period start)")
    ap.add_argument("--to", dest="date_to", help="Range end (default: today NZ, capped at period end)")
    ap.add_argument("--payroll-csv-dir", help="Folder of Jobman Custom Payroll CSV exports covering the range (optional)")
    ap.add_argument("--report-totals", action="store_true", help="GET the 4 saved Custom Payroll reports for a period cross-check")
    ap.add_argument("--staff-map", default=str(STAFF_MAP_PATH))
    ap.add_argument("--out-root", default=str(PROJECT / "out"))
    ap.add_argument("--cache-dir", default=str(PROJECT / "out" / "_activities_cache"))
    ap.add_argument("--cache-max-age-min", type=int, default=45)
    ap.add_argument("--task-cache-dir", default=str(PROJECT / "out" / "_tasks_cache"))
    ap.add_argument("--counted-from", choices=["jobman", "start_rule", "first_task"], default="jobman",
                    help="Counted-from rule for early-clock notes (default jobman: Aaron's three-way paid-start rule). "
                         "start_rule / first_task are legacy single-source overrides.")
    ap.add_argument("--task-starts", action="store_true", default=True,
                    help="GET task timesheet entries (default on; needed for the jobman paid-start rule)")
    ap.add_argument("--no-task-starts", action="store_false", dest="task_starts",
                    help="Skip fetching task starts (jobman rule then treats every early clock as unpaid early time)")
    ap.add_argument("--calibrate-rules", action="store_true", help="Fit staff_rules.json from the CSV totals in this run")
    args = ap.parse_args(argv)

    today = now_nz().date()
    r_to = date.fromisoformat(args.date_to) if args.date_to else today
    if args.period_start:
        p_start = date.fromisoformat(args.period_start)
        if fortnight_for(p_start)[0] != p_start:
            raise SystemExit(f"{p_start} is not a pay-fortnight Wednesday (anchor {FORTNIGHT_ANCHOR})")
        p_end = p_start + timedelta(days=13)
    else:
        p_start, p_end = fortnight_for(r_to)
    r_to = min(r_to, p_end)
    r_from = date.fromisoformat(args.date_from) if args.date_from else p_start
    if not (p_start <= r_from <= r_to <= p_end):
        raise SystemExit("Range must sit inside the fortnight")

    staff_map = json.loads(Path(args.staff_map).read_text())
    roster = [(n, tab) for tab in TAB_ORDER for n in staff_map.get(tab) or []]
    jm = Jobman()
    ids = load_staff_ids([n for n, _ in roster], jm)
    generated = now_nz()

    csv_rows = load_payroll_csv(Path(args.payroll_csv_dir)) if args.payroll_csv_dir else {}
    csv_dates = {d for (_, d) in csv_rows}
    csv_cover = (lambda d: bool(csv_rows) and min(csv_dates) <= d <= max(csv_dates))

    report = fetch_report_totals(jm) if args.report_totals else {}

    fin_by_staff = {}
    task_by_staff: dict[str, dict] = {}
    for name, tab in roster:
        acts = pull_activities(jm, ids[name], r_from, Path(args.cache_dir), args.cache_max_age_min)
        fin_by_staff[name] = finalised_days(acts, r_from, r_to)
        if args.task_starts or args.counted_from in ("jobman", "first_task"):
            ents = pull_task_entries(jm, ids[name], r_from, Path(args.task_cache_dir), args.cache_max_age_min)
            task_by_staff[name] = first_task_starts(ents, r_from, r_to)
        print(f"  {name}: {len(fin_by_staff[name])} Finalised day(s), "
              f"{len(task_by_staff.get(name, {}))} day(s) with task starts", flush=True)

    if args.calibrate_rules:
        if not csv_rows:
            raise SystemExit("--calibrate-rules needs --payroll-csv-dir")
        rules = calibrate([n for n, _ in roster], fin_by_staff, csv_rows, task_by_staff)
        RULES_PATH.write_text(json.dumps({"_about": (
            "Per-staff rules to ESTIMATE paid hours from Finalised clocks when no payroll CSV is available. "
            f"Fitted {generated.date()} against Custom Payroll CSV totals for {r_from} to {r_to}. "
            "paid_start: shift start used by the Jobman paid-start rule (if clock-on is before this and no task "
            "started before this, hours count from paid_start; if a task started before paid_start, early hours "
            "count). break_h: unpaid break deducted when the day is longer than break_after_h. "
            "Aaron confirmed paid-start rule 7 Oct 2026 NZ."), "rules": rules}, indent=2))
        print(f"Wrote {RULES_PATH}")
    rules = json.loads(RULES_PATH.read_text())["rules"] if RULES_PATH.exists() else {}

    out_dir = Path(args.out_root) / f"{p_start.isoformat()}_{p_end.isoformat()}"
    (out_dir / "staff").mkdir(parents=True, exist_ok=True)
    bundle = {"schema": SCHEMA, "kind": "staff-timesheet-view-bundle",
              "period": {"start": p_start.isoformat(), "end": p_end.isoformat(), "label": period_label(p_start, p_end)},
              "range": {"from": r_from.isoformat(), "to": r_to.isoformat()},
              "generated_at_nz": generated.isoformat(timespec="seconds"), "staff": {}}
    internal_all = []
    flat_rows = []
    for name, tab in roster:
        sid = ids[name]
        rule = rules.get(name, DEFAULT_RULE)
        staff_json, internal = build_staff(name, sid, tab, (p_start, p_end), (r_from, r_to), fin_by_staff[name],
                                           csv_rows, csv_cover, rule, report.get(sid), generated,
                                           first_task=task_by_staff.get(name), counted_pref=args.counted_from)
        (out_dir / "staff" / f"{sid}.json").write_text(json.dumps(staff_json, indent=2))
        bundle["staff"][sid] = staff_json
        internal_all.extend(internal)
        for drow in staff_json["days"]:
            flat_rows.append({"staff": name, "team": tab, "date": drow["date"], "weekday": drow["weekday_short"],
                              "status": drow["status"], "on": drow.get("on") or "", "off": drow.get("off") or "",
                              "worked": drow.get("worked_label") or "", "leave": "; ".join(f"{l['type']} {l['label']}" for l in drow.get("leave") or []),
                              "running_total": drow.get("running_total_label") or "", "note": drow.get("note") or ""})
    (out_dir / "bundle.json").write_text(json.dumps(bundle, indent=2))
    (out_dir / "internal_qa.json").write_text(json.dumps(internal_all, indent=2))
    with (out_dir / "days.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(flat_rows[0].keys()))
        w.writeheader()
        w.writerows(flat_rows)
    st = {}
    for i in internal_all:
        if i["status"] == "period":
            continue
        st[i["status"]] = st.get(i["status"], 0) + 1
    reasons = {}
    for i in internal_all:
        for rr in i.get("reasons") or []:
            reasons[rr] = reasons.get(rr, 0) + 1
    meta = {"generated_at_nz": generated.isoformat(timespec="seconds"),
            "period": [p_start.isoformat(), p_end.isoformat()], "range": [r_from.isoformat(), r_to.isoformat()],
            "staff": len(roster), "api_calls": jm.calls, "payroll_csv_dir": args.payroll_csv_dir,
            "payroll_csv_rows": len(csv_rows), "report_ranges": report.get("_ranges"),
            "day_status_counts": st, "being_checked_reasons": reasons,
            "out_dir": str(out_dir)}
    (out_dir / "run_meta.json").write_text(json.dumps(meta, indent=2, default=str))
    print(json.dumps(meta, indent=2, default=str))


if __name__ == "__main__":
    main()
