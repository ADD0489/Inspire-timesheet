#!/usr/bin/env python3
"""Copy the 'Request a change' log from Workers KV to the box (run nightly, or by hand).

Each request the Worker handles is stored as v1:req:<nz-date>:<ref> (sent or failed). This pulls any
new ones and appends them to out/requests/requests_log.jsonl and out/requests/requests_log.csv, so
there is a permanent local record alongside the copy in the sending mailbox's Sent Items.

  python3 pipeline/pull_requests.py --target local        # wrangler dev store
  python3 pipeline/pull_requests.py --target cloudflare   # live; needs CF_API_TOKEN etc.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from cf_kv import client  # noqa: E402

OUT = HERE.parent / "out" / "requests"
COLS = ["ref", "submitted_at_nz", "name", "team", "date", "jobman_on", "jobman_off", "jobman_status", "jobman_hours",
        "requested_start", "requested_finish", "reason", "to", "routed_because", "provider", "status", "error"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", choices=["local", "cloudflare"], default=os.environ.get("STV_PUSH_TARGET", "local"))
    a = ap.parse_args(argv)
    if a.target not in ("local", "cloudflare"):
        print(json.dumps({"skipped": True, "reason": f"target {a.target}"}))
        return
    OUT.mkdir(parents=True, exist_ok=True)
    jl = OUT / f"requests_log_{a.target}.jsonl"
    seen = set()
    if jl.exists():
        seen = {json.loads(l)["ref"] for l in jl.read_text().splitlines() if l.strip()}
    kv = client(a.target)
    new = []
    for k in kv.list_keys("v1:req:"):
        ref = k["name"].split(":")[-1]
        if ref in seen:
            continue
        v = kv.get(k["name"])
        if v:
            new.append(json.loads(v))
    new.sort(key=lambda r: r["ref"])
    with jl.open("a") as f:
        for r in new:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    rows = [json.loads(l) for l in jl.read_text().splitlines() if l.strip()]
    with (OUT / f"requests_log_{a.target}.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for r in rows:
            j, q = r.get("jobman_now") or {}, r.get("requested") or {}
            w.writerow({"ref": r["ref"], "submitted_at_nz": r.get("submitted_at_nz"), "name": r.get("name"),
                        "team": r.get("team"), "date": r.get("date"), "jobman_on": j.get("on"), "jobman_off": j.get("off"),
                        "jobman_status": j.get("status"), "jobman_hours": j.get("hours"),
                        "requested_start": q.get("start"), "requested_finish": q.get("finish"), "reason": r.get("reason"),
                        "to": r.get("to"), "routed_because": r.get("routed_because"), "provider": r.get("provider"),
                        "status": r.get("status"), "error": r.get("error")})
    print(json.dumps({"target": a.target, "new": len(new), "total": len(rows), "csv": str(OUT / f"requests_log_{a.target}.csv")}))


if __name__ == "__main__":
    main()
