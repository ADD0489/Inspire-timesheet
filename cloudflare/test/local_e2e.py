#!/usr/bin/env python3
"""End-to-end test against `wrangler dev --local` (http://127.0.0.1:8787) with local KV only.
MAIL_PROVIDER is "mock", so no email is sent; the rendered email is read back from local KV.
Raw tokens are read from secrets/ in memory and never printed.

Run: python3 cloudflare/test/local_e2e.py   (after push_cloudflare.py --target local and wrangler dev)
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))
from cf_kv import LocalKV  # noqa: E402

BASE = "http://127.0.0.1:8787"
IDS = json.loads((ROOT / "pipeline" / "staff_ids.json").read_text())
results = []


def tok(name):
    db = json.loads((ROOT / "secrets" / "stv_tokens.json").read_text())
    return next(t["token"] for t in db["tokens"] if t["staff_id"] == IDS[name] and t["status"] == "active")


def call(path, token=None, body=None, method="POST", ctype="application/json"):
    h = {"Content-Type": ctype}
    if token:
        h["Authorization"] = f"Bearer {token}"
    data = json.dumps(body or {}).encode() if method == "POST" else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"{}"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), dict(e.headers)


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


def main():
    kv0 = LocalKV()
    stale = [k["name"] for k in kv0.list_keys("v1:rl")]  # local test hygiene: reset today's rate-limit counters
    kv0.bulk_delete(stale)
    a = tok("Ashton Pienaar")
    s, j, h = call("/api/view", a)
    check("view: valid token gives 200 + data", s == 200 and j.get("status") == "ok" and j["previous"]["first_name"] == "Ashton", s)
    txt = json.dumps(j).lower()
    check("view: no overtime / ordinary / paid_from / staff_id in response",
          not any(k in txt for k in ("overtime", "ordinary", "paid_from", "staff_id", "target_h")))
    check("view: no-store + noindex headers", h.get("Cache-Control") == "no-store" and "noindex" in h.get("X-Robots-Tag", ""))
    check("view: change requests switched on", j["features"]["changeRequests"] is True)
    dn = tok("Daniyar Sarsembayev")
    s, jd, _ = call("/api/view", dn)
    d24 = next(d for d in jd["previous"]["days"] if d["date"] == "2026-09-24")
    check("view: early clock-on note data (Daniyar 24 Sep: 6:35am, counted from 7:00am, shift start; no task before 7:00)",
          d24.get("on") == "6:35am" and d24.get("counted_from") == "7:00am" and d24.get("counted_from_source") in ("shift_start", "start_rule"), d24)
    s, jr, _ = call("/api/request", dn, {"date": "2026-09-24", "start": "06:35", "finish": "16:45", "reason": "Forgot to start a task"})
    m = json.loads(LocalKV().get(f"v1:mailmock:{jr.get('ref')}") or "{}")
    check("request: email shows 'Hours count from 7:00am (usual start time)'",
          s == 200 and "Hours count from 7:00am (usual start time)" in m.get("text", ""), (s, jr))
    s1, j1, _ = call("/api/view", "x" * 32)
    s2, j2, _ = call("/api/view")
    check("view: bad token and missing token both 404 with the same body", s1 == s2 == 404 and j1 == j2)
    check("view: GET not allowed", call("/api/view", a, method="GET")[0] == 405)
    check("unknown /api path 404", call("/api/list", a)[0] == 404)

    good = {"date": "2026-09-30", "start": "06:45", "finish": "16:30", "reason": "Started early to load the truck"}
    s, j, _ = call("/api/request", a, good)
    check("request: valid request accepted", s == 200 and j.get("ok") and j["ref"].startswith("TS-"), (s, j))
    ref = j.get("ref")
    kv = LocalKV()
    mail = json.loads(kv.get(f"v1:mailmock:{ref}") or "{}")
    check("request: mock email stored, to Greg (AKL Operations), from company address",
          mail.get("to") == "Greg@inspirekitchens.co.nz" and mail.get("from", {}).get("address") == "timesheets@inspirekitchens.co.nz")
    check("request: email has name, date, Jobman times, requested times, reason",
          all(x in mail.get("text", "") for x in ("Ashton Pienaar", "Wednesday 30 September 2026", "In Jobman now: ",
                                                   "Requested start: 6:45am", "Requested finish: 4:30pm", "load the truck")))
    logs = [k["name"] for k in kv.list_keys("v1:req:")]
    check("request: logged in KV", any(ref in k for k in logs))
    Path(ROOT / "out" / "cloudflare" / "sample_email_ashton.json").write_text(json.dumps(mail, indent=2))

    bad = [({**good, "finish": "06:00"}, "order"), ({**good, "date": "2026-10-09"}, "bad_date"),
           ({**good, "date": "2026-09-10"}, "bad_date"), ({**good, "start": "6:45"}, "bad_time"),
           ({**good, "reason": "x" * 600}, "reason_long")]
    for body, code in bad:
        s, j, _ = call("/api/request", a, body)
        check(f"request: rejects {code}", s == 422 and j.get("code") == code, (s, j))
    check("request: bad token 404", call("/api/request", "y" * 32, good)[0] == 404)
    check("request: wrong content type 415", call("/api/request", a, good, ctype="text/plain")[0] == 415)

    d = tok("Duncan Nicholson")
    s, j, _ = call("/api/request", d, {"date": "2026-10-01", "start": "08:30", "finish": "17:00", "reason": ""})
    mail = json.loads(kv.get(f"v1:mailmock:{j.get('ref')}") or "{}")
    check("routing: Duncan (QT Sales + Dunc) goes to aaron@ while Dan is on leave",
          s == 200 and mail.get("to") == "aaron@inspirekitchens.co.nz" and "Dan on leave" in mail.get("text", ""), (s, j, mail.get("to")))
    Path(ROOT / "out" / "cloudflare" / "sample_email_duncan.json").write_text(json.dumps(mail, indent=2))

    jt = tok("Jordan Carlson")
    codes = []
    for i in range(6):
        s, j, _ = call("/api/request", jt, {"date": "2026-09-29", "start": f"07:{10 + i:02d}", "finish": "15:30"})
        codes.append(s)
    check("rate limit: 5 accepted then 429", codes[:5] == [200] * 5 and codes[5] == 429, codes)

    # Revoke and reissue Jordan's link through the real tools, then push tokens only
    py = sys.executable
    subprocess.run([py, str(ROOT / "tokens" / "stv_tokens.py"), "reissue", "--staff", "Jordan Carlson", "--reason", "e2e test"],
                   check=True, capture_output=True)
    subprocess.run([py, str(ROOT / "pipeline" / "push_cloudflare.py"), "--target", "local", "--tokens-only"], check=True,
                   capture_output=True)
    check("revoke: old link now 404", call("/api/view", jt)[0] == 404)
    check("reissue: new link works", call("/api/view", tok("Jordan Carlson"))[0] == 200)

    s = subprocess.run(["curl", "-sI", BASE + "/"], capture_output=True, text=True).stdout.lower()
    check("page: CSP frame-ancestors none + no-referrer", "frame-ancestors 'none'" in s and "referrer-policy: no-referrer" in s)
    n = sum(1 for _, ok in results if ok)
    print(f"\n{n}/{len(results)} passed")
    (ROOT / "out" / "cloudflare" / "local_e2e_results.json").write_text(json.dumps(results, indent=1))
    sys.exit(0 if n == len(results) else 1)


if __name__ == "__main__":
    main()
