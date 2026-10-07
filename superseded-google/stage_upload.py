#!/usr/bin/env python3
"""Stage the files the nightly routine uploads to the PRIVATE Drive data folder.

Copies out/<period>/bundle.json to out/upload/stv_bundle_<periodStart>_<yyyymmddHHMM>.json for each
period given, and writes out/upload/stv_tokens_<yyyymmddHHMM>.json (hashes only) from the private
token store. Apps Script always reads the newest file of each kind, so uploads never overwrite.
Usage: stage_upload.py 2026-10-07 [2026-09-23 ...]
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
UP = ROOT / "out" / "upload"


def main(periods):
    UP.mkdir(parents=True, exist_ok=True)
    for old in UP.glob("stv_*.json"):
        old.unlink()
    stamp = datetime.now(tz=ZoneInfo("Pacific/Auckland")).strftime("%Y%m%d%H%M")
    staged = []
    for p in periods:
        src = next((ROOT / "out").glob(f"{p}_*/bundle.json"))
        b = json.loads(src.read_text())
        assert b["kind"] == "staff-timesheet-view-bundle"
        dst = UP / f"stv_bundle_{p}_{stamp}.json"
        dst.write_text(json.dumps(b, separators=(",", ":")))
        staged.append(str(dst))
    tok = UP / f"stv_tokens_{stamp}.json"
    subprocess.run([sys.executable, str(ROOT / "tokens" / "stv_tokens.py"), "export-hashes", str(tok)], check=True,
                   stdout=subprocess.DEVNULL)
    staged.append(str(tok))
    print(json.dumps({"staged": staged}, indent=2))


if __name__ == "__main__":
    main(sys.argv[1:] or [])
