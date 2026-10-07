#!/usr/bin/env python3
"""Publish the Staff Timesheet View data to Workers KV (run by nightly.py, or by hand).

What gets written (keys documented in cloudflare/src/worker.js):
  v1:view:<staff_id>   staff-facing data for this fortnight and last fortnight (public_view.py, no
                       overtime, ordinary split, target or "paid from" fields)
  v1:tok:<sha256>      -> staff_id, for every ACTIVE token (raw tokens never leave the box)
  v1:config            config/change_requests.json (manager routing and request rules)
and every REVOKED token hash is deleted, so a revoked link stops working within about a minute.

Only keys whose value changed since the last push are written (state in out/cloudflare/), to stay far
inside the free plan's 1,000 KV writes a day. --force rewrites everything.

Targets: dry-run (default, writes the payload files only), local (wrangler local KV for `wrangler dev`),
cloudflare (live; needs CF_API_TOKEN, CF_ACCOUNT_ID, CF_KV_NAMESPACE_ID). The default can also be set
with the env var STV_PUSH_TARGET.

  python3 pipeline/push_cloudflare.py --target local
  python3 pipeline/push_cloudflare.py --target cloudflare --tokens-only     # fast revoke
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from public_view import assert_clean, public_staff  # noqa: E402

NZ = ZoneInfo("Pacific/Auckland")
TOKENS = ROOT / "secrets" / "stv_tokens.json"
CONFIG = ROOT / "config" / "change_requests.json"
STAFF_IDS = HERE / "staff_ids.json"
STAFF_TEAMS = Path("/workspace/staff_tab_map.json")
OUT = ROOT / "out" / "cloudflare"


def fortnight_for(day):
    from stv_pipeline import fortnight_for as f
    return f(day)


def bundle_for(start: str) -> dict | None:
    hits = sorted((ROOT / "out").glob(f"{start}_*/bundle.json"))
    return json.loads(hits[-1].read_text()) if hits else None


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def build(current: str | None, previous: str | None, tokens_only: bool):
    ids = json.loads(STAFF_IDS.read_text())
    puts: dict[str, str] = {}
    deletes: list[str] = []
    db = json.loads(TOKENS.read_text()) if TOKENS.exists() else {"tokens": []}
    active = {t["token_hash"] for t in db["tokens"] if t["status"] == "active"}
    for t in db["tokens"]:
        if t["status"] == "active":
            puts[f"v1:tok:{t['token_hash']}"] = t["staff_id"]
        elif t["token_hash"] not in active:
            deletes.append(f"v1:tok:{t['token_hash']}")
    if tokens_only:
        return puts, deletes, {}
    cfg = json.loads(CONFIG.read_text())
    cfg.pop("_about", None)
    teams = set(json.loads(STAFF_TEAMS.read_text())) - {"How to use"}
    unknown = set(cfg.get("managers", {})) - teams
    if unknown:
        raise SystemExit(f"config/change_requests.json has team names not in staff_tab_map.json: {unknown}")
    puts["v1:config"] = json.dumps(cfg, separators=(",", ":"))
    cur_b = bundle_for(current) if current else None
    prev_b = bundle_for(previous) if previous else None
    counts = {"current_bundle": bool(cur_b), "previous_bundle": bool(prev_b), "views": 0}
    for name, sid in ids.items():
        cur = public_staff((cur_b or {}).get("staff", {}).get(sid))
        prev = public_staff((prev_b or {}).get("staff", {}).get(sid))
        if not cur and not prev:
            continue
        v = {"current": cur, "previous": prev}
        assert_clean(v)
        puts[f"v1:view:{sid}"] = json.dumps(v, ensure_ascii=False, separators=(",", ":"))
        counts["views"] += 1
    return puts, deletes, counts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", choices=["dry-run", "local", "cloudflare"],
                    default=os.environ.get("STV_PUSH_TARGET", "dry-run"))
    ap.add_argument("--current", help="current fortnight start YYYY-MM-DD (default: today's fortnight)")
    ap.add_argument("--previous", help="previous fortnight start (default: the one before current)")
    ap.add_argument("--tokens-only", action="store_true", help="only token adds/revokes (fast revoke)")
    ap.add_argument("--force", action="store_true", help="write every key, not only changed ones")
    a = ap.parse_args(argv)

    today = datetime.now(tz=NZ).date()
    cs = a.current or fortnight_for(today)[0].isoformat()
    ps = a.previous or (datetime.fromisoformat(cs).date() - timedelta(days=14)).isoformat()
    if ps == "none":
        ps = None
    puts, deletes, counts = build(cs, ps, a.tokens_only)

    OUT.mkdir(parents=True, exist_ok=True)
    state_file = OUT / f"state_{a.target}.json"
    state = json.loads(state_file.read_text()) if state_file.exists() and not a.force else {"put": {}, "deleted": []}
    changed = [{"key": k, "value": v} for k, v in puts.items() if state["put"].get(k) != sha(v)]
    to_delete = [k for k in deletes if k not in state["deleted"]]
    stamp = datetime.now(tz=NZ).strftime("%Y%m%d%H%M")
    (OUT / "last_kv_put.json").write_text(json.dumps(changed, ensure_ascii=False))
    (OUT / "last_kv_delete.json").write_text(json.dumps(to_delete))

    summary = {"target": a.target, "current": cs, "previous": ps, **counts, "keys_total": len(puts),
               "writes": len(changed), "deletes": len(to_delete), "at_nz": stamp}
    if a.target != "dry-run":
        from cf_kv import client
        kv = client(a.target)
        kv.bulk_put(changed)
        kv.bulk_delete(to_delete)
        for it in changed:
            state["put"][it["key"]] = sha(it["value"])
        for k in to_delete:
            state["put"].pop(k, None)
            state["deleted"].append(k)
        state_file.write_text(json.dumps(state, indent=1))
    print(json.dumps(summary))
    return summary


if __name__ == "__main__":
    main()
