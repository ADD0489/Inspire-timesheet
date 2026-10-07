#!/usr/bin/env python3
"""Tiny Workers KV client used by push_cloudflare.py and pull_requests.py.

Targets:
  local       wrangler's local KV store (cloudflare/.wrangler/state), the same one `wrangler dev` uses.
              Nothing leaves the box.
  cloudflare  the real KV namespace via the Cloudflare REST API. Needs env vars CF_API_TOKEN,
              CF_ACCOUNT_ID, CF_KV_NAMESPACE_ID. The token only needs "Workers KV Storage: Edit"
              on Aaron's account.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CF_DIR = ROOT / "cloudflare"
NODE_BIN = os.environ.get("STV_NODE_BIN", "/workspace/tools/node22/bin")
API = "https://api.cloudflare.com/client/v4"


class LocalKV:
    def _wr(self, *args: str, capture=True) -> str:
        env = dict(os.environ, PATH=f"{NODE_BIN}:{os.environ.get('PATH', '')}", WRANGLER_SEND_METRICS="false",
                   CI="1")
        r = subprocess.run(["npx", "wrangler", "kv", *args, "--binding", "STV", "--local"], cwd=CF_DIR, env=env,
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"wrangler kv {' '.join(args)} failed: {r.stderr[-800:]}")
        return r.stdout

    def bulk_put(self, items: list[dict]) -> None:
        if not items:
            return
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(items, f)
        try:
            self._wr("bulk", "put", f.name)
        finally:
            os.unlink(f.name)

    def bulk_delete(self, keys: list[str]) -> None:
        if not keys:
            return
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(keys, f)
        try:
            self._wr("bulk", "delete", f.name, "--force")
        finally:
            os.unlink(f.name)

    def list_keys(self, prefix: str) -> list[dict]:
        out = self._wr("key", "list", "--prefix", prefix)
        return json.loads(out[out.index("["):]) if "[" in out else []

    def get(self, key: str) -> str | None:
        try:
            return self._wr("key", "get", key, "--text")
        except RuntimeError:
            return None


class CloudflareKV:
    def __init__(self):
        missing = [k for k in ("CF_API_TOKEN", "CF_ACCOUNT_ID", "CF_KV_NAMESPACE_ID") if not os.environ.get(k)]
        if missing:
            raise SystemExit(f"Missing env vars for the cloudflare target: {', '.join(missing)}")
        self.tok = os.environ["CF_API_TOKEN"]
        self.base = (f"{API}/accounts/{os.environ['CF_ACCOUNT_ID']}/storage/kv/namespaces/"
                     f"{os.environ['CF_KV_NAMESPACE_ID']}")

    def _req(self, method: str, path: str, body=None, raw=False):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.tok}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            txt = r.read().decode()
        if raw:
            return txt
        j = json.loads(txt)
        if not j.get("success", False):
            raise RuntimeError(f"Cloudflare API error on {method} {path}: {j.get('errors')}")
        return j

    def bulk_put(self, items: list[dict]) -> None:
        for i in range(0, len(items), 5000):
            self._req("PUT", "/bulk", items[i:i + 5000])

    def bulk_delete(self, keys: list[str]) -> None:
        for i in range(0, len(keys), 5000):
            self._req("POST", "/bulk/delete", keys[i:i + 5000])

    def list_keys(self, prefix: str) -> list[dict]:
        keys, cursor = [], ""
        while True:
            q = urllib.parse.urlencode({"prefix": prefix, "limit": 1000, **({"cursor": cursor} if cursor else {})})
            j = self._req("GET", f"/keys?{q}")
            keys += j.get("result", [])
            cursor = (j.get("result_info") or {}).get("cursor") or ""
            if not cursor:
                return keys

    def get(self, key: str) -> str | None:
        try:
            return self._req("GET", "/values/" + urllib.parse.quote(key, safe=""), raw=True)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise


def client(target: str):
    if target == "local":
        return LocalKV()
    if target == "cloudflare":
        return CloudflareKV()
    raise ValueError(target)
