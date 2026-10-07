#!/usr/bin/env python3
"""Nightly refresh for the Staff Timesheet View (run by the scheduled routine on the box).

1. Current fortnight, from its Wednesday to today: Finalised clocks + estimate rules
   (+ the saved-report cross-check when its range matches).
2. Previous fortnight: re-run on the first 3 days of a new fortnight (late Finalised entries) and
   whenever its Custom Payroll CSVs exist (the fortnightly payroll routine saves them to
   /workspace/timesheet-template/exports-<d><mon>-<d><mon>/payroll-csv), so last fortnight shows
   payroll totals once they are exported.
3. Publish to Cloudflare Workers KV with push_cloudflare.py (only changed keys), then copy any new
   "Request a change" log entries back to the box with pull_requests.py.
   Target comes from STV_PUSH_TARGET: dry-run (default, nothing leaves the box), local (wrangler dev
   store) or cloudflare (live; needs CF_API_TOKEN, CF_ACCOUNT_ID, CF_KV_NAMESPACE_ID).
Jobman is only read (GET).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from stv_pipeline import fortnight_for  # noqa: E402

NZ = ZoneInfo("Pacific/Auckland")


def csv_dir_for(start, end) -> Path | None:
    tag = f"{start.day}{start.strftime('%b').lower()}-{end.day}{end.strftime('%b').lower()}"
    p = Path(f"/workspace/timesheet-template/exports-{tag}/payroll-csv")
    return p if p.exists() and any(p.glob("*.csv")) else None


def run(args):
    print("+", " ".join(args), flush=True)
    subprocess.run([sys.executable, str(HERE / "stv_pipeline.py")] + args, check=True)


def main():
    today = datetime.now(tz=NZ).date()
    cs, ce = fortnight_for(today)
    ps, pe = cs - timedelta(days=14), cs - timedelta(days=1)
    run(["--period-start", cs.isoformat(), "--to", today.isoformat(), "--report-totals", "--cache-max-age-min", "0"])
    periods = [cs.isoformat()]
    pcsv = csv_dir_for(ps, pe)
    if (today - cs).days <= 2 or pcsv:
        a = ["--period-start", ps.isoformat(), "--to", pe.isoformat(), "--report-totals", "--cache-max-age-min", "0"]
        if pcsv:
            a += ["--payroll-csv-dir", str(pcsv)]
        run(a)
        periods.append(ps.isoformat())
    elif any((HERE.parent / "out").glob(f"{ps.isoformat()}_*/bundle.json")):
        periods.append(ps.isoformat())  # keep last fortnight available on the page
    target = os.environ.get("STV_PUSH_TARGET", "dry-run")
    push = [sys.executable, str(HERE / "push_cloudflare.py"), "--target", target, "--current", cs.isoformat()]
    if ps.isoformat() in periods:
        push += ["--previous", ps.isoformat()]
    else:
        push += ["--previous", "none"]
    subprocess.run(push, check=True)
    if target in ("local", "cloudflare"):
        subprocess.run([sys.executable, str(HERE / "pull_requests.py"), "--target", target], check=False)
    print(json.dumps({"ok": True, "push_target": target, "current": cs.isoformat(), "previous_included": ps.isoformat() in periods,
                      "previous_payroll_csv": str(pcsv) if pcsv else None}))


if __name__ == "__main__":
    main()
