# Staff Timesheet View v2: hosting, setup and operations

Version 2, 7 Oct 2026. Replaces the Google Apps Script plan (Aaron has no Google Workspace; the old
files are in `superseded-google/`). Nothing has been deployed and no accounts have been created.
Everything below has been built and tested on the box only.

## 1. What staff get

- A private link per person, opened on their phone with no login: `https://inspire-timesheets.<subdomain>.workers.dev/#t=<token>`.
- **Logged hours by day, plus the fortnight total.** Each day shows the exact clock-on and clock-off
  times from Jobman, that day's logged hours and the running total. When someone clocks on 10 or more minutes
  before their hours start counting, a short note shows under the times, for example "Clocked on 6:35am; hours
  count from 7:00am (the usual start time). Forgot to start a task? Request a change." (see section 11). Weekends are greyed. Leave shows as a
  label (and "Includes Xh of leave" under the total). "Being checked" days are labelled. "Last updated" time
  and the footer stay.
- **Removed:** overtime, the ordinary/overtime split, the 80h bar and the "Paid from 7:00am" wording. These
  fields are stripped in `pipeline/public_view.py` before upload, so they never reach Cloudflare or the phone
  (a test fails if any of them slip through).
- **This fortnight / Last fortnight** tabs (pay fortnights run Wednesday to Tuesday, anchored on 23 Sep 2026).
- **Request a change:** a button on each day and one in the footer. The person picks the day, enters the
  correct start and finish (exact minutes), and an optional reason. Their manager gets an email from
  timesheets@inspirekitchens.co.nz. Staff see a confirmation with a reference number. See section 6.

## 2. Hosting: options and recommendation

| | Cloudflare Workers + KV (recommended) | Microsoft 365 (OneDrive / SharePoint) | Netlify Functions (or similar) |
|---|---|---|---|
| Private link, no login, any phone | Yes | Only with "Anyone" links, which the SharePoint admin can switch off or force to expire | Yes |
| Can render our page | Yes | No. OneDrive and SharePoint download HTML files instead of showing them. A SharePoint page cannot be shared anonymously | Yes |
| Token hashing, instant revoke | Yes (SHA-256 hash lookup, delete one KV key) | No. Each link is a OneDrive share; 16 links made by hand in the OneDrive UI (our OneDrive tool cannot create share links) | Yes, but needs a separate data store |
| Change request emails | Yes (Worker calls Microsoft Graph) | Would need Power Automate (premium HTTP trigger for anonymous use) | Yes |
| Free plan limits vs our use | 100,000 requests/day, KV 1,000 writes/day and 100,000 reads/day. We use about 50 writes and a few hundred reads a day | Included in M365 | 300 credits/month hard cap; the site pauses when used up |
| Cost | NZ$0/month (paid plan US$5/month only if ever needed) | Included | NZ$0, but the cap is a risk |

**Recommendation: Cloudflare Workers + KV (free plan), with the static page on Workers Static Assets.**
It is the only option that keeps the design intact (hashed tokens, instant revoke, a real mobile page,
no logins) at no cost and with lots of headroom. Microsoft 365 is not realistic for a page like this because
it will not render HTML for anonymous viewers. Netlify works, but its free credit cap can pause the site and
it would still need a separate data store. Vercel Hobby is for non-commercial use only.

How it works (`cloudflare/`):
- `public/` is the page (index.html, app.js, app.css). `_headers` sets a strict Content Security Policy,
  `frame-ancestors 'none'`, no-referrer and noindex. `robots.txt` blocks indexing.
- `src/worker.js` answers only `POST /api/view` and `POST /api/request`. The token is read from the URL
  fragment (never sent to the server or written to logs) and sent in the `Authorization` header. The Worker
  hashes it with SHA-256 and looks up `v1:tok:<hash>` in KV. Bad, revoked and missing tokens all get the
  same 404. There is no endpoint that lists staff or tokens.
- KV holds only: token hashes, each person's staff-facing data, the change-request config, rate-limit counters
  and the request log. Raw tokens stay in `secrets/stv_tokens.json` on the box.

## 3. Email for "Request a change": options and recommendation

| | Microsoft Graph sendMail (recommended) | Resend free | Cloudflare Email Service |
|---|---|---|---|
| Sends from a company address | Yes, from a shared mailbox timesheets@ (free, no licence) | Yes, after adding DNS records (SPF/DKIM) to inspirekitchens.co.nz | Needs the domain's DNS on Cloudflare and the Workers Paid plan (US$5/month) for normal sending |
| Delivery to managers | Internal Microsoft 365 to Microsoft 365, so no spam-folder risk | Good once the domain is verified | Good |
| DNS changes | None | Yes | Yes (moving DNS or MX is risky with M365 mail) |
| Record kept | Copy in the mailbox's Sent Items, plus our log | Resend dashboard (30 days), plus our log | Our log |
| Cost | Included in M365 | Free up to 100 emails/day, 3,000/month | US$5/month plan |

**Recommendation: Microsoft Graph sendMail from a new shared mailbox `timesheets@inspirekitchens.co.nz`.**
All four recipients are internal, so mail stays inside Inspire's own Microsoft 365, needs no DNS changes and
costs nothing. The app is locked to that one mailbox with Exchange "RBAC for Applications"
(`email/setup_graph_mailer.ps1`), so it cannot send as anyone else. Resend is coded in as the fallback
(`MAIL_PROVIDER = "resend"`). MailChannels' free Workers sending ended in 2024.

The Worker ships with `MAIL_PROVIDER = "mock"`: it never sends, and it stores the rendered email in KV for
checking. All testing so far used mock mode. **No real email has been sent.**

## 4. What Aaron needs to set up (about 45 minutes, once)

**A. Cloudflare (about 15 minutes)**
1. Sign up for a free account at dash.cloudflare.com (any email, for example aaron@). No card needed.
2. Workers & Pages: open it once and pick a workers.dev subdomain (for example `inspirekitchens`).
3. Storage & Databases > KV > Create namespace, name it `stv`. Copy the namespace ID.
4. Copy the Account ID (shown on the Workers & Pages overview page).
5. My Profile > API Tokens > Create Token:
   - **Token 1 (nightly data push, keep):** Custom token, permission *Account > Workers KV Storage > Edit*,
     Account Resources: *Include > Inspire's account only*. Cloudflare cannot narrow this to one namespace;
     the account will only hold this one namespace. No expiry, or one year with a reminder.
   - **Token 2 (one-off deploy, short-lived):** template *Edit Cloudflare Workers*, this account only,
     TTL ending tomorrow. Used once for the first deploy and the secrets, then it expires.
6. Paste the Account ID, namespace ID and both tokens into the box chat (or a file Aaron puts on the box).
   I then set `CF_ACCOUNT_ID`, `CF_KV_NAMESPACE_ID` and `CF_API_TOKEN` (token 1) in the box's private env file,
   put the namespace ID in `cloudflare/wrangler.toml`, and run the deploy with token 2.

**B. Microsoft 365 email (about 30 minutes, needs a Global or Exchange admin)**
1. Exchange admin centre: add a shared mailbox `timesheets@inspirekitchens.co.nz` (display name
   "Inspire Kitchens Timesheets").
2. Entra admin centre > App registrations > New: "Inspire Timesheets Mailer", single tenant. Copy the tenant
   ID and client ID. Add a client secret (24 months; diary a reminder to renew). Do **not** add Mail.Send
   under API permissions.
3. Run `email/setup_graph_mailer.ps1` in PowerShell (scopes the app to that one mailbox and tests it).
4. Paste the tenant ID, client ID and secret into the box. They go into Cloudflare as Worker secrets
   (`wrangler secret put GRAPH_TENANT_ID` etc.), never into code or KV.

**C. Go-live (done from the box once A and B are in place; needs Aaron's go-ahead)**
1. `npx wrangler deploy` (token 2), then `wrangler secret put` for the three Graph values.
2. Send one test request to Aaron only: temporarily set every team in `config/change_requests.json` to
   aaron@, push, submit one request from a test link, check it arrives, then restore the real mapping.
3. Switch `MAIL_PROVIDER` from `mock` to `graph` in `wrangler.toml` and redeploy.
4. Set `STV_PUSH_TARGET=cloudflare` for the nightly routine.
5. Links go out to the pilot group by whatever channel Aaron chooses (we do not send them).

## 5. Monthly cost

| Item | Cost |
|---|---|
| Cloudflare Workers + KV + Static Assets, free plan | NZ$0 |
| Microsoft Graph sendMail and a shared mailbox (already in M365) | NZ$0 |
| Custom domain (optional, e.g. timesheets.inspirekitchens.co.nz) | NZ$0 if DNS moves to Cloudflare, otherwise not needed |
| **Total** | **NZ$0 per month** |

Headroom: about 50 KV writes a day from the nightly push (only changed keys are written) plus 4 per change
request, against 1,000 free. Page views use 2 to 3 KV reads each, against 100,000 free. If Inspire ever
outgrows it, Workers Paid is US$5/month (about NZ$9).

## 6. "Request a change": rules and records

- Config: `config/change_requests.json` (published to KV on every push). Team names match
  `/workspace/staff_tab_map.json`:
  - AKL Operations: Greg@inspirekitchens.co.nz
  - AKL Sales: aaron@inspirekitchens.co.nz
  - QT Sales + Dunc: Dan@inspirekitchens.co.nz, with a cover entry sending to aaron@ from 1 Oct to 16 Oct
    2026 inclusive (Dan on leave). Change the `until` date if Dan is back on the 16th.
  - QT Ops: qtops@inspirekitchens.co.nz
  - Unknown team: fallback aaron@.
- Days allowed: any day from this fortnight or last fortnight, up to today (switch off last fortnight with
  `allow_previous_fortnight`). The Worker checks the date against that person's own published data, not
  the phone's copy.
- Times: HH:MM, finish after start, at most 16 hours, not identical to what Jobman already has.
  Exact minutes are allowed; set `time_step_minutes` to 15 to force quarter hours.
- Reason: optional, up to 500 characters, HTML-escaped in the email.
- Rate limits: 5 requests per person per NZ day, 60 for everyone per day (protects the mailbox). KV counters
  are approximate under very fast repeated taps, which is fine at this scale.
- The email (sample in `preview/sample-email-v2.png` and `.txt`) includes staff name and team, the day, what
  Jobman has now, requested start and finish, start-to-finish span, reason, NZ submission time and a
  reference. It says nothing has been changed automatically and that staff cannot see replies.
- Records: every request (sent or failed) is stored as `v1:req:<date>:<ref>` in KV with no expiry.
  `pipeline/pull_requests.py` copies new ones nightly to `out/requests/requests_log_<target>.csv`
  and `.jsonl`. With Graph, the sent email is also in the shared mailbox's Sent Items.
- Managers still make the change in Jobman (the API cannot write clock times, and Jobman stays read-only for us).
  The page shows the new times after the next nightly refresh.

## 7. Nightly refresh (box routine)

- 7:30pm NZ plus a 10:00pm catch-up (Jobman auto clock-offs for 6:00pm shifts finalise about 9pm).
- `python3 pipeline/nightly.py`: pulls Jobman (35 to 45 GET calls, under the 50/min limit), builds the bundles,
  then `push_cloudflare.py` (target from `STV_PUSH_TARGET`: dry-run by default, `local`, or `cloudflare`)
  and `pull_requests.py`.
- `push_cloudflare.py` writes only keys that changed since the last push (state in `out/cloudflare/`),
  and deletes the hash of every revoked token. `--force` rewrites everything. `--tokens-only` is the fast revoke.
- If a push fails the page keeps showing the previous night's data with its "Last updated" time.

## 8. Link tokens

`tokens/stv_tokens.py` (unchanged scheme: `secrets.token_urlsafe(24)`, 192 bits; raw tokens only in
`secrets/stv_tokens.json`, mode 600):
- `issue --all` / `issue --staff "Name"`, `list` (masked), `link --staff "Name" --base https://...workers.dev`
  (prints `.../#t=<token>`; only when delivery is approved).
- `revoke --staff "Name"` or `reissue --staff "Name"`, then
  `python3 pipeline/push_cloudflare.py --target cloudflare --tokens-only`. The old link stops working within
  about a minute (Worker cache 60 seconds plus KV propagation up to 60 seconds).
- Tested locally: reissuing Jordan Carlson's token made the old link return 404 and the new one work.
  (Jordan's local token was reissued as part of that test. No token has ever been sent to anyone.)

## 9. Privacy points

- The page and API never return another person's data; the data for each person is keyed by staff ID and
  only reachable with that person's token.
- No payroll fields (overtime, ordinary split, paid-from) are published.
- Tokens are in the URL fragment, so they are not in server logs or Referer headers. Responses are no-store
  and noindex; the page cannot be framed.
- Cloudflare stores names, teams and clock times for 16 staff. Requests add the reason text. Request logs in
  KV have no expiry; prune yearly if wanted.
- Anyone who has a person's link can see that person's hours and send requests in their name (limited to 5 a
  day, and the manager sees the details). Staff should keep links private; reissue if one is shared.

## 10. Local testing done on the box (7 Oct 2026, about 3:30pm NZ)

- Node 22 and wrangler 4.148.0 installed under `/workspace/tools` and `cloudflare/node_modules`
  (telemetry off). `wrangler dev --local` ran with local KV only.
- `node --test test/requests.test.mjs`: 10/10 pass (re-run 7 Oct, 4:30pm NZ) (validation, 15-minute rule, routing incl. Dan's cover,
  HTML escaping, no em dashes).
- `python3 cloudflare/test/local_e2e.py`: 25/25 pass (incl. the early clock-on note and its email line; valid/invalid/missing token, method and path checks, no
  payroll fields, request accepted, mock email to Greg from timesheets@, routing Duncan to aaron@, 5 bad-input
  cases, rate limit 5 then 429, revoke and reissue, security headers).
- To repeat: `cd cloudflare && PATH=/workspace/tools/node22/bin:$PATH npm run dev`, then in another shell
  `python3 pipeline/push_cloudflare.py --target local --force` and `python3 cloudflare/test/local_e2e.py`.
  The rate-limit test needs a fresh day (or a cleared `.wrangler/state`) to pass again.

## 11. How the numbers are worked out (unchanged from v1)

- Clock on/off: Jobman "Finalised timesheet entries" lines (earliest on, latest off; >16h segments dropped).
- Day hours: Jobman's Custom Payroll CSV total when exported; otherwise the nightly estimate from clocks and
  `pipeline/staff_rules.json`. The page notes when hours come from clock times.
- "Hours count from" note (Jobman paid-start rule, Aaron confirmed 7 Oct 2026 NZ): the day figure stays Jobman's
  logged hours. Counted start uses `paid_start` in `pipeline/staff_rules.json` (7:00am for AKL Operations, 8:00am
  for Madi) plus the day's first task start (GET /staff/{id}/timesheet, fetched by default):
  (1) clock at/after shift start -> from clock-on (no note);
  (2) clock before shift AND a task started before shift -> early hours paid from max(clock, first task);
      no note if the task started within 10 min of clock-on, otherwise note says "when your first task started";
  (3) clock before shift AND no task before shift -> hours from shift start, show the note ("the usual start time").
  Staff with no `paid_start` are counted from clock-on. Legacy `--counted-from start_rule|first_task` still works;
  default is `jobman`. Use `--no-task-starts` only if you must skip the task GETs (rule then always clips early clocks).
- "Being checked": sources disagree by 15 min or more, paid with no clocks, absurd or split clock entries,
  over 14h, or a 6:00pm auto clock-off.

## 12. Pilot and known data gaps (unchanged from v1)

- Pilot suggestion: AKL Operations (cleanest data), two fortnights alongside the approval sheet.
- Leave entered only in the approval sheet never shows (23 Sep to 6 Oct: Daniyar 23 Sep AL 8h, Jacques 29 Sep sick 8h).
- Manager corrections made only in the approval sheet do not flow back.
- Rules in `staff_rules.json` were fitted on one fortnight; Rachel or Aaron should confirm start times and breaks.
