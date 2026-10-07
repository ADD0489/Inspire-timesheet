# Staff Timesheet View v2 (Cloudflare, with "Request a change")

Staff open a private link on their phone (no login) and see their own logged hours by day and the
fortnight total, straight from Jobman. They can send a "Request a change" email to their manager.
Jobman stays the only clock and is only read. Not deployed yet; see `DEPLOYMENT_AND_OPERATIONS.md`.

| Path | What it is |
|---|---|
| `pipeline/stv_pipeline.py`, `nightly.py` | Jobman pull (GET only) and per-staff data build |
| `pipeline/public_view.py` | Staff-facing projection (strips overtime, ordinary, paid-from) |
| `pipeline/push_cloudflare.py`, `cf_kv.py` | Publish data, token hashes and config to Workers KV (dry-run / local / cloudflare) |
| `pipeline/pull_requests.py` | Copy the change-request log back to `out/requests/` |
| `config/change_requests.json` | Manager email per team, Dan's cover dates, request rules |
| `cloudflare/` | The Worker (`src/`), page (`public/`), `wrangler.toml`, tests (`test/`) |
| `email/setup_graph_mailer.ps1` | One-off Exchange setup so the Worker can send only as timesheets@ |
| `tokens/stv_tokens.py` | Issue, list, revoke, reissue, link (raw tokens only in `secrets/`) |
| `preview/` | v2 phone previews, request form, confirmation and sample email |
| `superseded-google/` | Old Apps Script version, not used |

No em dashes anywhere, NZ English throughout.
