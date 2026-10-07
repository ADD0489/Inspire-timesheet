# Staff Timesheet View (MVP): deployment and operations plan

Inspire Kitchens. Prepared for Aaron Dykstra, 7 Oct 2026 (NZDT). VIEW ONLY.
Jobman stays the only clock and is only ever read (GET requests). Nothing has been sent to staff,
no links exist outside the box, and nothing has been deployed to Google yet.

## 1. What staff get

A private link that opens a phone-friendly page (no Google login) showing:
- their name and the pay fortnight (Wed to Tue, e.g. 7 Oct to 20 Oct 2026)
- a big fortnight-to-date total, a progress bar against 80h, and Ordinary / Overtime / Leave chips
- a day-by-day list: clock on, clock off, hours, running total, weekends greyed, leave labelled,
  "Being checked" days marked (no times or doubtful hours shown for those)
- "Last updated ... (NZ time)", plus "Something look wrong? Please speak to your manager."
- a "Last fortnight" tab, which matters on the first days of a new fortnight (like today)

Previews (real data, 23 Sep to 6 Oct 2026, phone width): `preview/*.png`.

## 2. How the numbers are worked out

| Item | Source | Notes |
|---|---|---|
| Clock on / off | Jobman activity log "Finalised timesheet entries" (API) | Earliest on, latest off; >16h segments dropped (same rule as the approval sheet builder) |
| Day hours, Ordinary, Overtime, leave | Custom Payroll CSV export when it exists (browser export) | CSV clock columns are ignored (known Jobman bug) |
| Day hours when no CSV (normal nightly case) | Estimate from Finalised clocks using `pipeline/staff_rules.json` | Paid start time (e.g. 7:00am for AKL Operations) and 30 min unpaid break. Fitted on 23 Sep to 6 Oct. The page then says hours are confirmed when the fortnight is approved |
| Fortnight cross-check | Saved Jobman Custom Payroll reports (API) | Only when the report's saved range equals the fortnight (GET cannot change the range) |

Overtime = hours over 8 in a day, the same rule the approval sheet uses to seed Overtime.

A day is **Being checked** when: payroll and clock hours disagree by 15 min or more; hours are paid
with no clocks; a clock entry is absurd (>16h), has a gap, or spans over 14h; or the clock-off is exactly
6:00pm (Jobman's auto clock-off records End Shift, which hid missed clock-offs for Anna, Andrea and Clebson).
Being-checked days never show clock times. Their hours count only if they come from payroll totals,
are 14h or less, and the fortnight total agrees with Jobman's saved report; otherwise "Not in your total yet".

**Clock display: exact times (to the minute), not snapped.** Staff recognise the time they saw on the kiosk,
snapping can move a time by up to 7 minutes and would trigger "that is not when I clocked on" questions,
and the 15-minute snap is an approval-sheet dropdown rule, not a pay rule. Where Jobman pays from a set start
time, the page shows "6:51am to 3:54pm, Paid from 7:00am" so the gap is explained. Snapped values are in the
data; set Script property `DISPLAY_SNAP=true` to switch.

## 3. Nightly refresh

- **Times: 7:30pm NZ, plus a 10:00pm catch-up.** Jobman writes the Finalised line when someone clocks off.
  Auto clock-off for people who forget happens about 3 hours after End Shift, so 6:00pm shifts are not
  finalised until about 9pm. 7:30pm catches most staff the same evening; 10:00pm catches the rest.
- **Cost: about 35 to 45 Jobman GET calls per run** (16 staff), paced at 45/min (limit 50/min). About 1 minute.
- **Routine steps (scheduled on my side, on the box):**
  1. `python3 /workspace/staff-timesheet-view/pipeline/nightly.py`
     (current fortnight to today; previous fortnight in the first 3 days of a fortnight or once its payroll CSVs exist;
     then stages `out/upload/stv_bundle_<start>_<stamp>.json` and `stv_tokens_<stamp>.json`)
  2. Upload each staged file to the private Drive data folder (UploadFile to Google Drive, folderId of that folder).
     Files are new each night; the page always reads the newest. No overwrite of anything.
  3. Trash files in that folder older than 3 days (the routine's own files only).
  4. Post a 3-line health note to Aaron only if something failed or more than 25% of worked days are "being checked".
- The Drive tools cannot edit a Google Sheet's cells in place, which is why the data store is a folder of JSON
  files rather than a Sheet. `data-sheet/Staff Timesheet View DATA (DRAFT).xlsx` is a human-readable mirror if wanted.

## 4. Google and browser steps (need Aaron, about 20 minutes, box browser or his laptop)

1. **Choose the hosting account** (decision). The web app runs as this account. Recommended: a company
   Workspace account (aaron@inspirekitchens.co.nz, or better a shared admin account so it does not depend on one person).
   Not the personal gmail.
2. In that account's **My Drive** (not the shared drive), create a private folder "Staff Timesheet View (private)". Share with nobody.
3. script.google.com > New project "Inspire Staff Timesheet View". Add files `Code.gs`, `Page.html`, `Message.html`,
   and replace `appsscript.json` (Project Settings > Show manifest) with the one in `apps-script/`.
4. Project Settings > Script properties: `DATA_FOLDER_ID` = the folder id.
5. Upload the staged files from `out/upload/` into the folder (I can do this once the folder exists).
6. Run `adminCheckSetup` once from the editor and approve the Drive read-only permission.
7. Deploy > New deployment > Web app: Execute as **Me**, Who has access **Anyone**. Copy the /exec URL.
   If "Anyone" is not offered, the Workspace admin has blocked public web apps (Admin console > Apps > Google
   Workspace > Drive and Docs > sharing outside the domain); this must be allowed for staff without Google accounts.
8. Test with Aaron's own pilot link on his phone (I print it with `tokens/stv_tokens.py link`; it is not sent anywhere).
Google may show a small grey notice at the top of the page for people outside the company domain. That is normal.

## 5. Link tokens

- `tokens/stv_tokens.py`: 192-bit random tokens (`secrets.token_urlsafe(24)`). 16 pilot tokens were issued
  **on the box only** today; nobody has a link.
- Raw tokens: only in `secrets/stv_tokens.json` (folder 700, file 600). Google gets SHA-256 hashes only
  (`stv_tokens_<stamp>.json`), so a leaked data file cannot be turned into links.
- The page never contains a token, staff id, or anyone else's data. Bad, revoked and missing tokens all get the same
  "This link is not working" page, so there is no way to probe for names.
- Revoke: `stv_tokens.py revoke --staff "Name"` then the next upload (or immediately: run `adminRevokeStaff('<staff id>')`
  in the Apps Script editor). Reissue: `stv_tokens.py reissue --staff "Name"`, publish hashes, send the new link.
- Lost phone or leaving staff: revoke the same day.

## 6. Privacy points

- Data shown: own name, own clocks and hours only. No pay rates, no money, no contact details.
- Data folder must be private (My Drive). The "3 Operations" folder in the shared drive is shared with about 26
  accounts, including several staff in this pilot and outside addresses, so I did **not** create the DATA Sheet there.
- Script scope is Drive **read-only**: the public page cannot change or delete anything.
- No tokens are logged; failed attempts are counted only.
- A link works for anyone holding it (like a Google "anyone with the link" file). Tell staff to keep it private;
  SMS and personal email are fine for that. Links can be revoked at any time.
- Staff should be told in plain words what the page is (view only, from Jobman) before links go out.
- Keep the box token file out of Drive, email and chat.

## 7. Pilot

Suggested: one team for two fortnights alongside the approval sheet. AKL Operations (7 staff) is the
cleanest data. Tested on 23 Sep to 6 Oct in nightly (estimate-only) mode against Jobman's fortnight totals:
Ashton -3 min, Patrick 0, Jacques -9, Ben +29, Nili -36, Bruce +73, Daniyar -84 (Jacques and Bruce each had one
day held back as being checked). QT Ops is also good apart from Clebson (Agustin 0, Matthew -1, Gladys -82).
QT Sales + Dunc is the messiest (6:00pm auto clock-offs for Anna and Andrea, Duncan's Finalised break pattern,
Duncan +118 min) and should wait. Full table: `out/_eval_estimate_only/estimate_vs_report.json`.

## 8. Phase 2: "Request a change"

The page already has a hidden "Request a change" button on each worked day (`SHOW_CHANGE_REQUESTS=true` shows it;
`doPost` is a stub). Plan:
1. Staff tap a day, choose what is wrong (start, finish, leave, missing day), enter the right time and a reason.
2. `doPost` (same token check) writes the request to a private Requests sheet (needs the spreadsheets scope) with
   staff, date, current values, requested values, reason, status Pending.
3. Approver comes from `staff_tab_map.json`: AKL Operations to Greg, AKL Sales to Aaron, QT Sales + Dunc to Dan,
   QT Ops to qtops. One daily digest per approver (Outlook or Teams), with approve or decline links (approver tokens).
4. Approved changes are made in Jobman by the manager (the API cannot write clock-on/off), so Jobman stays the record.
   The fortnightly approval sheet build adds approved requests to the Notes column.
5. Staff see "Change requested", then "Approved" or "Declined" on that day.

## 9. Known data gaps

- No API source for daily paid totals or daily leave. Nightly hours are estimates until the payroll CSV is exported.
- Leave entered only in the approval sheet (not in Jobman) never shows. 23 Sep to 6 Oct: Daniyar 23 Sep annual leave 8h,
  Jacques 29 Sep sick leave 8h.
- Manager corrections made only in the approval sheet do not flow back (Matthew 23 Sep and 5 Oct finish times).
- Jobman Timesheet UI is more reliable than Finalised on some days (Clebson 23 Sep: UI 1:00pm, Finalised 1:30pm) but is browser-only.
- Rules in `staff_rules.json` were fitted on one fortnight; Rachel or Aaron should confirm start times and breaks.
- Only the 16 staff on the approval sheet are covered. The other active Jobman staff are not in the payroll reports.
