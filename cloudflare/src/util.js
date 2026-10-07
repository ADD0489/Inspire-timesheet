// Small helpers shared by the Worker. No dependencies.

export const NZ_TZ = 'Pacific/Auckland';

export async function sha256Hex(text) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

export function nzDate(d = new Date()) {
  // YYYY-MM-DD in New Zealand time
  return new Intl.DateTimeFormat('en-CA', { timeZone: NZ_TZ, year: 'numeric', month: '2-digit', day: '2-digit' }).format(d);
}

export function nzStamp(d = new Date()) {
  // e.g. "Wed 7 Oct 2026, 3:42pm"
  const p = Object.fromEntries(new Intl.DateTimeFormat('en-NZ', {
    timeZone: NZ_TZ, weekday: 'short', day: 'numeric', month: 'short', year: 'numeric',
    hour: 'numeric', minute: '2-digit', hour12: true,
  }).formatToParts(d).map((x) => [x.type, x.value]));
  return `${p.weekday} ${p.day} ${p.month} ${p.year}, ${p.hour}:${p.minute}${(p.dayPeriod || '').toLowerCase().replace(/\./g, '').replace(/\s/g, '')}`;
}

export function longDate(iso) {
  // "2026-09-30" -> "Wednesday 30 September 2026" (date only, no time zone shift)
  const [y, m, d] = iso.split('-').map(Number);
  return new Intl.DateTimeFormat('en-NZ', { timeZone: 'UTC', weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })
    .format(new Date(Date.UTC(y, m - 1, d))).replace(/,/g, '');
}

export function hhmmTo12(t) {
  // "15:30" -> "3:30pm"
  const [h, m] = t.split(':').map(Number);
  const ap = h >= 12 ? 'pm' : 'am';
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `${h12}:${String(m).padStart(2, '0')}${ap}`;
}

export function minutes(t) {
  const [h, m] = t.split(':').map(Number);
  return h * 60 + m;
}

export function spanLabel(mins) {
  return `${Math.floor(mins / 60)}h ${String(mins % 60).padStart(2, '0')}m`;
}

export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

export function refId(date = new Date()) {
  const alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  const r = crypto.getRandomValues(new Uint8Array(5));
  return `TS-${nzDate(date).replace(/-/g, '')}-${[...r].map((b) => alphabet[b % alphabet.length]).join('')}`;
}

export function shortDate(iso) {
  // "2026-10-16" -> "16 Oct 2026"
  const [y, m, d] = iso.split('-').map(Number);
  return new Intl.DateTimeFormat('en-NZ', { timeZone: 'UTC', day: 'numeric', month: 'short', year: 'numeric' })
    .format(new Date(Date.UTC(y, m - 1, d)));
}
