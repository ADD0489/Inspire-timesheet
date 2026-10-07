// "Request a change": validation, manager routing and the email text. Pure functions, so they can
// be unit tested in Node without Cloudflare (see test/requests.test.mjs).
import { esc, hhmmTo12, longDate, minutes, shortDate, spanLabel } from './util.js';

export const MESSAGES = {
  disabled: 'Change requests are switched off at the moment. Please speak to your manager.',
  bad_date: 'Please choose a day from this fortnight or last fortnight, up to today.',
  bad_time: 'Please enter both a start time and a finish time.',
  step: (n) => `Please use times in ${n}-minute steps, for example 7:00, 7:15 or 7:30.`,
  order: 'The finish time needs to be after the start time.',
  too_long: (h) => `That is longer than ${h} hours. Please check the times.`,
  same: 'Those times are the same as Jobman already has for that day.',
  reason_long: (n) => `Please keep the reason under ${n} characters.`,
  limit: (n) => `You have sent ${n} requests today, which is the daily limit. Please speak to your manager.`,
  busy: 'Requests are paused for today. Please speak to your manager.',
  send_failed: 'Sorry, your request could not be sent just now. Please try again later, or speak to your manager.',
};

const TIME_RE = /^([01]\d|2[0-3]):([0-5]\d)$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

export function cleanReason(s) {
  return String(s ?? '')
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, '')
    .replace(/\r\n?/g, '\n')
    .trim();
}

/** Find the day in the staff member's own published data. Never trust the browser's copy. */
export function findDay(view, date, rules) {
  const sources = [['current', view && view.current]];
  if (rules.allow_previous_fortnight) sources.push(['previous', view && view.previous]);
  for (const [which, s] of sources) {
    if (!s) continue;
    const d = (s.days || []).find((x) => x.date === date);
    if (d) return { which, staff: s, day: d };
  }
  return null;
}

export function validate(body, view, config, today) {
  const rules = config.rules || {};
  if (!config.enabled) return { ok: false, code: 'disabled', message: MESSAGES.disabled };
  const date = String(body.date || '');
  const start = String(body.start || '');
  const finish = String(body.finish || '');
  const reason = cleanReason(body.reason);

  if (!DATE_RE.test(date) || date > today) return { ok: false, code: 'bad_date', message: MESSAGES.bad_date };
  const found = findDay(view, date, rules);
  if (!found || !found.day.in_range || found.day.status === 'future') {
    return { ok: false, code: 'bad_date', message: MESSAGES.bad_date };
  }
  if (!TIME_RE.test(start) || !TIME_RE.test(finish)) return { ok: false, code: 'bad_time', message: MESSAGES.bad_time };
  const step = Number(rules.time_step_minutes || 1);
  if (step > 1 && (minutes(start) % step || minutes(finish) % step)) {
    return { ok: false, code: 'step', message: MESSAGES.step(step) };
  }
  const span = minutes(finish) - minutes(start);
  if (span <= 0) return { ok: false, code: 'order', message: MESSAGES.order };
  const maxH = Number(rules.max_shift_hours || 16);
  if (span > maxH * 60) return { ok: false, code: 'too_long', message: MESSAGES.too_long(maxH) };
  const d = found.day;
  if (d.on_exact === start && d.off_exact === finish) return { ok: false, code: 'same', message: MESSAGES.same };
  const maxR = Number(rules.reason_max_chars || 500);
  if (reason.length > maxR) return { ok: false, code: 'reason_long', message: MESSAGES.reason_long(maxR) };

  return { ok: true, date, start, finish, reason, span, found };
}

/** Manager address for a team on a given NZ date. Cover entries (leave) win while they apply. */
export function routeManager(team, config, today) {
  const m = (config.managers || {})[team];
  if (!m) return { to: config.fallback_to, why: 'no manager set for this team, sent to the fallback' };
  for (const c of m.cover || []) {
    if ((!c.from || today >= c.from) && (!c.until || today <= c.until)) {
      return { to: c.to, why: `${c.why || 'cover'}${c.until ? ', cover set until ' + shortDate(c.until) : ''}` };
    }
  }
  return { to: m.to, why: '' };
}

export function jobmanNow(d) {
  const parts = [];
  const times = d.on && d.off ? `${d.on} to ${d.off}` : '';
  if (d.status === 'ok') parts.push(times ? `${times} (${d.worked_label || 'no hours'} logged)` : 'Hours logged, no clock times');
  if (d.status === 'ok' && d.counted_from) {
    const src = d.counted_from_source === 'first_task' ? 'first task started' : 'usual start time';
    // first_task | shift_start | legacy start_rule (usual start time covers shift_start and start_rule)
    parts.push(`Hours count from ${d.counted_from} (${src})`);
  }
  else if (d.status === 'being_checked') parts.push(`${times ? times + ', ' : ''}being checked${d.note ? ': ' + d.note : ''}`);
  else if (d.status === 'today_pending') parts.push(times ? `${times} (not finalised yet)` : 'Not finalised yet');
  else parts.push('No clock entry');
  for (const l of d.leave || []) parts.push(`Leave: ${l.type} ${l.label}`);
  const out = parts.join('. ');
  return out.charAt(0).toUpperCase() + out.slice(1);
}

export function buildEmail({ staff, day, req, ref, submittedLabel, route }) {
  const dateLong = longDate(req.date);
  const now = jobmanNow(day);
  const span = `${spanLabel(req.span)}, before any unpaid break`;
  const reason = req.reason || 'No reason given.';
  const subject = `Timesheet change request: ${staff.name}, ${dateLong}`;
  const rows = [
    ['Staff member', `${staff.name} (${staff.team})`],
    ['Day', dateLong],
    ['In Jobman now', now],
    ['Requested start', hhmmTo12(req.start)],
    ['Requested finish', hhmmTo12(req.finish)],
    ['Start to finish', span],
    ['Reason', reason],
    ['Sent', `${submittedLabel} (NZ time)`],
    ['Reference', ref],
  ];
  const text = [
    'Kia ora,',
    '',
    `${staff.name} has asked for a shift adjustment or correction in Jobman.`,
    '',
    ...rows.map(([k, v]) => `${k}: ${v}`),
    '',
    'If the request is right, please update the timesheet in Jobman. Nothing has been changed automatically, and the staff page will show the new times after the next nightly refresh.',
    'Staff do not get a copy of this email and cannot see replies, so please follow up with them in person or by phone.',
    ...(route.why ? ['', `Routed to you because: ${route.why}.`] : []),
    '',
    'Sent by the Inspire Kitchens staff timesheet page. Please do not reply to this address.',
  ].join('\n');
  const html = `<!DOCTYPE html><html lang="en-NZ"><body style="margin:0;padding:16px;background:#f4f5f7;font-family:Segoe UI,Arial,sans-serif;color:#1d2433">
<div style="max-width:560px;margin:0 auto;background:#fff;border-radius:12px;padding:20px">
<p style="margin:0 0 4px;font-size:12px;letter-spacing:.04em;text-transform:uppercase;color:#5d6679">Inspire Kitchens timesheets</p>
<h2 style="margin:0 0 12px;font-size:20px">Timesheet change request</h2>
<p style="margin:0 0 14px">Kia ora,<br>${esc(staff.name)} has asked for a shift adjustment or correction in Jobman.</p>
<table style="border-collapse:collapse;width:100%;font-size:15px">
${rows.map(([k, v]) => `<tr><td style="padding:7px 10px 7px 0;border-top:1px solid #e6e8ee;color:#5d6679;vertical-align:top;white-space:nowrap">${esc(k)}</td><td style="padding:7px 0;border-top:1px solid #e6e8ee;vertical-align:top;${k.startsWith('Requested') ? 'font-weight:700;' : ''}">${esc(v).replace(/\n/g, '<br>')}</td></tr>`).join('\n')}
</table>
<p style="margin:16px 0 8px">If the request is right, please update the timesheet in Jobman. Nothing has been changed automatically, and the staff page will show the new times after the next nightly refresh.</p>
<p style="margin:0 0 8px">Staff do not get a copy of this email and cannot see replies, so please follow up with them in person or by phone.</p>
${route.why ? `<p style="margin:0 0 8px;color:#5d6679;font-size:13px">Routed to you because: ${esc(route.why)}.</p>` : ''}
<p style="margin:14px 0 0;color:#5d6679;font-size:12px">Sent by the Inspire Kitchens staff timesheet page. Please do not reply to this address.</p>
</div></body></html>`;
  return { subject, text, html };
}
