#!/usr/bin/env python3
"""Per-staff secret link tokens for the Staff Timesheet View.

- Tokens are random and unguessable: secrets.token_urlsafe(24) = 32 URL-safe chars, 192 bits.
- The raw tokens live ONLY in the private file secrets/stv_tokens.json on the box (chmod 600).
  They are needed to build each person's link when delivery is switched on.
- Cloudflare only ever gets SHA-256 hashes (pipeline/push_cloudflare.py writes v1:tok:<hash>), so a
  leaked KV export cannot be turned back into working links. The page never contains a staff list.
- Links look like https://<worker>.workers.dev/#t=<token>. The part after # is never sent to the
  server or kept in its logs; the page sends the token only in a POST header.
- Revoke = mark revoked here, then push: the hash is deleted from KV and the link stops working
  within about a minute. Reissue = revoke the old token and issue a new one.

Commands:
  issue   --all | --staff "Name"     issue tokens for staff without an active one
  reissue --staff "Name"             revoke the current token and issue a new one
  revoke  --staff "Name"             revoke (no new token)
  list                               show status per staff (tokens masked)
  export-hashes OUT.json             hashes + staff_id + status (no raw tokens), for checking
  link --staff "Name" --base URL     print one person's link (only when delivery is approved)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

NZ = ZoneInfo("Pacific/Auckland")
HERE = Path(__file__).resolve().parent
STORE = HERE.parent / "secrets" / "stv_tokens.json"
STAFF_IDS = HERE.parent / "pipeline" / "staff_ids.json"


def now() -> str:
    return datetime.now(tz=NZ).isoformat(timespec="seconds")


def h(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def load() -> dict:
    if STORE.exists():
        return json.loads(STORE.read_text())
    return {"_about": "PRIVATE. Raw link tokens. Never upload, email or paste this file.", "tokens": []}


def save(db: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(STORE.parent, 0o700)
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(db, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(STORE)


def staff_ids() -> dict[str, str]:
    return json.loads(STAFF_IDS.read_text())


def active_for(db, sid):
    return [t for t in db["tokens"] if t["staff_id"] == sid and t["status"] == "active"]


def issue_one(db, name, sid, reason="issued"):
    tok = secrets.token_urlsafe(24)
    db["tokens"].append({"staff_id": sid, "name": name, "token": tok, "token_hash": h(tok),
                         "status": "active", "issued_at": now(), "revoked_at": None, "reason": reason})
    return tok


def revoke_all(db, sid, reason):
    n = 0
    for t in active_for(db, sid):
        t["status"] = "revoked"
        t["revoked_at"] = now()
        t["revoke_reason"] = reason
        n += 1
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("issue", "reissue", "revoke"):
        p = sub.add_parser(c)
        p.add_argument("--staff")
        p.add_argument("--all", action="store_true")
        p.add_argument("--reason", default="")
    sub.add_parser("list")
    p = sub.add_parser("export-hashes")
    p.add_argument("out")
    p = sub.add_parser("link")
    p.add_argument("--staff", required=True)
    p.add_argument("--base", required=True, help="Worker URL, e.g. https://inspire-timesheets.<subdomain>.workers.dev")
    a = ap.parse_args(argv)
    db = load()
    ids = staff_ids()

    def targets():
        if getattr(a, "all", False):
            return list(ids.items())
        if not a.staff or a.staff not in ids:
            sys.exit(f"Unknown staff {a.staff!r}. Known: {', '.join(ids)}")
        return [(a.staff, ids[a.staff])]

    if a.cmd == "issue":
        n = 0
        for name, sid in targets():
            if not active_for(db, sid):
                issue_one(db, name, sid)
                n += 1
        save(db)
        print(f"Issued {n} token(s). Raw tokens are only in {STORE}.")
    elif a.cmd == "reissue":
        for name, sid in targets():
            revoke_all(db, sid, a.reason or "reissued")
            issue_one(db, name, sid, "reissue")
        save(db)
        print("Reissued. Now run: python3 pipeline/push_cloudflare.py --target cloudflare --tokens-only "
              "(the old link stops working within about a minute).")
    elif a.cmd == "revoke":
        for name, sid in targets():
            print(f"{name}: revoked {revoke_all(db, sid, a.reason or 'revoked')} token(s)")
        save(db)
        print("Now run: python3 pipeline/push_cloudflare.py --target cloudflare --tokens-only "
              "(the link stops working within about a minute).")
    elif a.cmd == "list":
        for name, sid in ids.items():
            act = active_for(db, sid)
            rev = [t for t in db["tokens"] if t["staff_id"] == sid and t["status"] == "revoked"]
            mask = (act[0]["token"][:4] + "..." ) if act else "-"
            print(f"{name:22} active={len(act)} revoked={len(rev)} token={mask} issued={act[0]['issued_at'] if act else '-'}")
    elif a.cmd == "export-hashes":
        out = {"schema": 1, "kind": "staff-timesheet-view-tokens", "generated_at_nz": now(),
               "tokens": [{"token_hash": t["token_hash"], "staff_id": t["staff_id"], "status": t["status"]}
                          for t in db["tokens"]]}
        Path(a.out).write_text(json.dumps(out, indent=2))
        print(f"Wrote {a.out} ({len(out['tokens'])} hashes, no raw tokens, no names)")
    elif a.cmd == "link":
        sid = ids.get(a.staff)
        act = active_for(db, sid) if sid else []
        if not act:
            sys.exit("No active token")
        print(f"{a.base.rstrip('/')}/#t={act[0]['token']}")


if __name__ == "__main__":
    main()
