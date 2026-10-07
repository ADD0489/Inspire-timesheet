#!/usr/bin/env python3
"""Render static previews of apps-script/Page.html (the exact file Apps Script serves) for a few
real staff, then take phone-width PNG screenshots with headless Chrome via Playwright.
The JSON is injected exactly as Code.gs does it (safeJson_), so previews match the live page."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "apps-script" / "Page.html"
OUT = ROOT / "preview"
PREV = ROOT / "out" / "2026-09-23_2026-10-06" / "bundle.json"
CUR = ROOT / "out" / "2026-10-07_2026-10-20" / "bundle.json"


def safe_json(obj) -> str:
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return (s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
             .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def public(s):
    if not s:
        return None
    s = json.loads(json.dumps(s))
    s.pop("staff_id", None)
    return s


def render(name: str, slug: str, current, previous):
    payload = {"current": public(current), "previous": public(previous),
               "features": {"changeRequests": False, "displaySnap": False}}
    html = PAGE.read_text().replace("<?!= payloadJson ?>", safe_json(payload))
    assert "<?" not in html, "unrendered scriptlet"
    p = OUT / f"{slug}.html"
    p.write_text(html)
    return p


def main():
    prev = json.loads(PREV.read_text())["staff"]
    cur = json.loads(CUR.read_text())["staff"] if CUR.exists() else {}
    by_name = {v["name"]: (k, v) for k, v in prev.items()}
    jobs = []
    for name, slug in [("Ashton Pienaar", "ashton-23sep-6oct"), ("Duncan Nicholson", "duncan-23sep-6oct"),
                       ("Clebson Almeida", "clebson-23sep-6oct")]:
        sid, data = by_name[name]
        jobs.append(render(name, slug, data, None))
    # Same person as staff would see it today (7 Oct): new fortnight just started, last fortnight tab shown
    sid, data = by_name["Ashton Pienaar"]
    jobs.append(render("Ashton Pienaar", "ashton-today-7oct", cur.get(sid), data))

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
        for p in jobs:
            page = ctx.new_page()
            page.goto(p.as_uri())
            page.wait_for_timeout(300)
            png = p.with_suffix(".png")
            page.screenshot(path=str(png), full_page=True)
            # also the first screen only (what they see on opening the link)
            page.screenshot(path=str(p.with_name(p.stem + "-first-screen.png")), full_page=False)
            print("wrote", png)
            page.close()
        b.close()


if __name__ == "__main__":
    main()
