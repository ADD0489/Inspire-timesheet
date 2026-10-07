#!/usr/bin/env python3
"""Cross-check STV pipeline output against the corrected manager approval sheet (xlsx export).

Read-only: the sheet is a local xlsx downloaded from Drive. Compares, per staff-day:
  clocks: pipeline Finalised clocks snapped to 15 min vs sheet Clocked-on / Clocked-off
  overtime: pipeline (payroll Total - Ordinary - leave) vs sheet Overtime (1 minute tolerance)
  leave: pipeline leave hours vs sheet leave columns
Being-checked days show no clocks to staff, so they are counted separately ("held back").
Usage: compare_to_sheet.py SHEET.xlsx OUT_DIR(with internal_qa.json)
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

import openpyxl

TABS = ["AKL Operations", "AKL Sales", "QT Sales + Dunc", "QT Ops"]
LEAVE_COLS = {7: "Annual Leave", 8: "Bereavement Leave", 9: "Sick Leave", 10: "Unpaid Leave"}


def hrs(v) -> float:
    if v is None or v == "":
        return 0.0
    if isinstance(v, timedelta):
        return v.total_seconds() / 3600
    if isinstance(v, time):
        return v.hour + v.minute / 60
    try:
        return float(v) * 24 if float(v) < 1.5 else float(v)
    except (TypeError, ValueError):
        return 0.0


def hm(t) -> str | None:
    if t is None:
        return None
    if isinstance(t, datetime):
        t = t.time()
    return t.strftime("%H:%M")


def mdiff(a: str | None, b: str | None) -> int | None:
    if not a or not b:
        return None
    ah, am = map(int, a.split(":"))
    bh, bm = map(int, b.split(":"))
    return (ah * 60 + am) - (bh * 60 + bm)


def main(sheet_path: str, out_dir: str):
    wb = openpyxl.load_workbook(sheet_path, data_only=False)
    sheet: dict[tuple[str, str], list[dict]] = {}
    for tab in TABS:
        ws = wb[tab]
        for r in ws.iter_rows(min_row=2, values_only=True):
            if not r[1] or r[2] is None:
                continue
            d = r[2].date() if isinstance(r[2], datetime) else r[2]
            sheet.setdefault((r[1], d.isoformat()), []).append({
                "on": hm(r[3]), "off": hm(r[4]), "ot_h": hrs(r[6]),
                "leave_h": sum(hrs(r[i]) for i in LEAVE_COLS), "note": r[11] or ""})
    qa = [i for i in json.loads((Path(out_dir) / "internal_qa.json").read_text()) if i.get("date") != "PERIOD"]
    res = []
    for i in qa:
        rows = sheet.get((i["staff"], i["date"])) or [{"on": None, "off": None, "ot_h": 0.0, "leave_h": 0.0, "note": "(no sheet row)"}]
        s = rows[0]
        leave_mine = sum((i.get("leave") or {}).values())
        sheet_has = bool(s["on"] or s["off"] or s["ot_h"] > 0.01 or s["leave_h"] > 0.01)
        mine_has = i["status"] != "no_entry"
        mine_on_paid = i.get("on_snap15")
        if mine_on_paid and i.get("paid_start") and mine_on_paid < i["paid_start"]:
            mine_on_paid = i["paid_start"]
        rec = {"staff": i["staff"], "date": i["date"], "status": i["status"], "reasons": i["reasons"],
               "mine_on_paid_start": mine_on_paid, "mine_leave_h": leave_mine, "sheet_leave_h": round(s["leave_h"], 3),
               "sheet_on": s["on"], "sheet_off": s["off"], "sheet_ot_h": round(s["ot_h"], 3),
               "mine_on": i.get("on_snap15"), "mine_off": i.get("off_snap15"), "mine_ot_h": i.get("overtime_h"),
               "sheet_rows": len(rows), "sheet_note": s["note"][:140]}
        if not sheet_has and not mine_has:
            rec["result"] = "match_empty"
        elif i["status"] == "being_checked":
            would = (rec["mine_on"] == s["on"] and rec["mine_off"] == s["off"])
            rec["result"] = "held_back_being_checked"
            rec["finalised_would_have_matched_clocks"] = would
        else:
            don, doff = mdiff(rec["mine_on"], s["on"]), mdiff(rec["mine_off"], s["off"])
            clocks_ok = rec["mine_on"] == s["on"] and rec["mine_off"] == s["off"]
            ot_ok = abs((i.get("overtime_h") or 0) - s["ot_h"]) <= 1 / 60 + 1e-6
            lv_ok = abs(leave_mine - s["leave_h"]) <= 1 / 60 + 1e-6
            within15 = (don is not None and doff is not None and abs(don) <= 15 and abs(doff) <= 15)
            rec.update({"on_diff_min": don, "off_diff_min": doff, "ot_ok": ot_ok, "leave_ok": lv_ok,
                        "rows_ok": len(rows) == 1})
            paid_ok = mine_on_paid == s["on"] and rec["mine_off"] == s["off"]
            rec["match_with_paid_start_rule"] = bool(paid_ok and ot_ok and lv_ok and len(rows) == 1)
            if clocks_ok and ot_ok and lv_ok and len(rows) == 1:
                rec["result"] = "match"
            elif within15 and ot_ok and lv_ok and len(rows) == 1:
                rec["result"] = "match_within_15m"
            else:
                rec["result"] = "mismatch"
        res.append(rec)
    cnt = {}
    for r in res:
        cnt[r["result"]] = cnt.get(r["result"], 0) + 1
    worked = [r for r in res if r["result"] != "match_empty"]
    shown = [r for r in worked if r["result"] != "held_back_being_checked"]
    summary = {
        "sheet": sheet_path, "staff_days_total": len(res), "counts": cnt,
        "strict_match_all_days": cnt.get("match", 0) + cnt.get("match_empty", 0),
        "worked_days": len(worked),
        "worked_days_strict_match": cnt.get("match", 0),
        "worked_days_match_within_15m": cnt.get("match", 0) + cnt.get("match_within_15m", 0),
        "worked_days_shown_to_staff": len(shown),
        "worked_days_match_if_clock_on_shown_as_paid_start": sum(1 for r in res if r.get("match_with_paid_start_rule")),
        "held_back_being_checked": cnt.get("held_back_being_checked", 0),
        "held_back_where_finalised_clocks_disagreed_with_sheet": sum(1 for r in worked if r["result"] == "held_back_being_checked" and not r["finalised_would_have_matched_clocks"]),
        "mismatches": [r for r in res if r["result"] == "mismatch"],
        "within_15m": [r for r in res if r["result"] == "match_within_15m"],
    }
    (Path(out_dir) / "compare_to_corrected_sheet.json").write_text(json.dumps({"summary": summary, "rows": res}, indent=2, default=str))
    s2 = {k: v for k, v in summary.items() if k not in ("mismatches", "within_15m")}
    print(json.dumps(s2, indent=2))
    print("MISMATCHES:")
    for r in summary["mismatches"]:
        print(f"  {r['staff'][:16]:16} {r['date']} mine {r['mine_on']}-{r['mine_off']} OT {r['mine_ot_h']} | sheet {r['sheet_on']}-{r['sheet_off']} OT {r['sheet_ot_h']} leave mine/sheet {r['mine_leave_h']}/{r['sheet_leave_h']} rows={r['sheet_rows']} | {r['sheet_note'][:90]}")
    print("WITHIN 15m:")
    for r in summary["within_15m"]:
        print(f"  {r['staff'][:16]:16} {r['date']} mine {r['mine_on']}-{r['mine_off']} | sheet {r['sheet_on']}-{r['sheet_off']} | {r['sheet_note'][:80]}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
