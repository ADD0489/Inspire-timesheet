// Inspire Kitchens: Staff Timesheet View on Cloudflare Workers (free plan).
//
// Static page (public/) is served by Workers Static Assets. This Worker only answers /api/*:
//   POST /api/view     Authorization: Bearer <token>  -> the person's own two fortnights
//   POST /api/request  Authorization: Bearer <token>  -> "Request a change" email to their manager
//
// KV layout (namespace bound as STV, written nightly by pipeline/push_cloudflare.py):
//   v1:tok:<sha256(token)>   -> staff_id          (only hashes; raw tokens stay on the box)
//   v1:view:<staff_id>       -> {current, previous} staff-facing data, no payroll fields
//   v1:config                -> config/change_requests.json (manager routing and rules)
//   v1:rl:<staff_id>:<date>  -> requests sent today (rate limit), expires after 2 days
//   v1:rlall:<date>          -> requests sent today by everyone
//   v1:req:<date>:<ref>      -> request log (kept), pulled to the box by pipeline/pull_requests.py
// Bad, revoked and missing tokens all get the same 404. There is no listing endpoint.

import { nzDate, nzStamp, refId, sha256Hex } from './util.js';
import { MESSAGES, buildEmail, routeManager, validate } from './requests.js';
import { sendMail } from './mailer.js';

const BASE_HEADERS = {
  'Content-Type': 'application/json; charset=utf-8',
  'Cache-Control': 'no-store',
  'X-Robots-Tag': 'noindex, nofollow',
  'Referrer-Policy': 'no-referrer',
  'X-Content-Type-Options': 'nosniff',
};
const TOKEN_RE = /^Bearer ([A-Za-z0-9_-]{20,128})$/;
const MAX_BODY = 4096;
const DEFAULT_CONFIG = { enabled: false, managers: {}, rules: {} };

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), { status, headers: BASE_HEADERS });
}
const notFound = () => json({ error: 'not_found' }, 404);

async function staffFor(request, env) {
  const m = TOKEN_RE.exec(request.headers.get('Authorization') || '');
  if (!m) return null;
  const hash = await sha256Hex(m[1]);
  return env.STV.get(`v1:tok:${hash}`, { cacheTtl: 60 });
}

async function loadConfig(env) {
  return (await env.STV.get('v1:config', { type: 'json', cacheTtl: 60 })) || DEFAULT_CONFIG;
}

async function readBody(request) {
  const len = Number(request.headers.get('Content-Length') || 0);
  if (len > MAX_BODY) return null;
  const text = await request.text();
  if (text.length > MAX_BODY) return null;
  if (!text) return {};
  try { const b = JSON.parse(text); return b && typeof b === 'object' ? b : null; } catch { return null; }
}

async function handleView(request, env) {
  const staffId = await staffFor(request, env);
  if (!staffId) return notFound();
  const [view, config] = await Promise.all([
    env.STV.get(`v1:view:${staffId}`, { type: 'json', cacheTtl: 60 }),
    loadConfig(env),
  ]);
  const r = config.rules || {};
  const features = {
    changeRequests: !!config.enabled,
    timeStepMinutes: Number(r.time_step_minutes || 1),
    maxPerDay: Number(r.per_staff_per_day || 5),
    allowPreviousFortnight: !!r.allow_previous_fortnight,
    reasonMaxChars: Number(r.reason_max_chars || 500),
    today: nzDate(),
  };
  if (!view) return json({ status: 'not_ready', features });
  return json({ status: 'ok', current: view.current || null, previous: view.previous || null, features });
}

async function handleRequest(request, env) {
  const staffId = await staffFor(request, env);
  if (!staffId) return notFound();
  if (!(request.headers.get('Content-Type') || '').startsWith('application/json')) return json({ error: 'bad_request' }, 415);
  const body = await readBody(request);
  if (!body) return json({ error: 'bad_request' }, 400);

  const today = nzDate();
  const [view, config] = await Promise.all([env.STV.get(`v1:view:${staffId}`, { type: 'json' }), loadConfig(env)]);
  if (!view) return json({ ok: false, code: 'bad_date', message: MESSAGES.bad_date }, 422);
  const v = validate(body, view, config, today);
  if (!v.ok) return json(v, 422);

  const rules = config.rules || {};
  const perStaff = Number(rules.per_staff_per_day || 5);
  const perAll = Number(rules.all_staff_per_day || 60);
  const rlKey = `v1:rl:${staffId}:${today}`;
  const rlAllKey = `v1:rlall:${today}`;
  const [mine, all] = await Promise.all([env.STV.get(rlKey), env.STV.get(rlAllKey)]);
  const nMine = Number(mine || 0), nAll = Number(all || 0);
  if (nMine >= perStaff) return json({ ok: false, code: 'limit', message: MESSAGES.limit(perStaff) }, 429);
  if (nAll >= perAll) return json({ ok: false, code: 'busy', message: MESSAGES.busy }, 429);
  await Promise.all([
    env.STV.put(rlKey, String(nMine + 1), { expirationTtl: 172800 }),
    env.STV.put(rlAllKey, String(nAll + 1), { expirationTtl: 172800 }),
  ]);

  const staff = v.found.staff;
  const ref = refId();
  const submittedLabel = nzStamp();
  const route = routeManager(staff.team, config, today);
  const email = buildEmail({ staff, day: v.found.day, req: v, ref, submittedLabel, route });
  const from = config.from || { address: env.MAIL_SENDER, name: 'Inspire Kitchens Timesheets' };

  const log = {
    ref, submitted_at_nz: submittedLabel, submitted_date_nz: today, staff_id: staffId, name: staff.name, team: staff.team,
    date: v.date, jobman_now: { on: v.found.day.on_exact || null, off: v.found.day.off_exact || null, status: v.found.day.status, hours: v.found.day.worked_label || null },
    requested: { start: v.start, finish: v.finish }, reason: v.reason, to: route.to, routed_because: route.why || null,
    provider: (env.MAIL_PROVIDER || 'mock').toLowerCase(), status: 'pending',
  };
  try {
    const sent = await sendMail(env, { ref, from, to: route.to, ...email });
    log.status = 'sent';
    log.provider = sent.provider;
    log.message_id = sent.id;
  } catch (e) {
    log.status = 'failed';
    log.error = String(e && e.message || e).slice(0, 200);
  }
  await env.STV.put(`v1:req:${today}:${ref}`, JSON.stringify(log), {
    metadata: { staff: staffId, date: v.date, status: log.status },
  });
  console.log(`[request] ${ref} ${log.status} staff=${staffId} date=${v.date}`);

  if (log.status !== 'sent') return json({ ok: false, code: 'send_failed', message: MESSAGES.send_failed, ref }, 502);
  return json({ ok: true, ref, remaining_today: Math.max(0, perStaff - nMine - 1), sent_label: submittedLabel });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === '/api/view' || url.pathname === '/api/request') {
      if (request.method !== 'POST') return json({ error: 'method_not_allowed' }, 405);
      try {
        return url.pathname === '/api/view' ? await handleView(request, env) : await handleRequest(request, env);
      } catch (e) {
        console.log(`[error] ${url.pathname} ${e && e.message}`);
        return json({ error: 'server_error' }, 500);
      }
    }
    if (url.pathname.startsWith('/api/')) return notFound();
    return env.ASSETS.fetch(request);
  },
};
