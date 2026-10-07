// node --test test/  (Node 22). Pure-function tests for validation, routing and the email text.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { validate, routeManager, buildEmail } from '../src/requests.js';

const cfg = JSON.parse(readFileSync(new URL('../../config/change_requests.json', import.meta.url)));
const day = (date, extra = {}) => ({ date, day_label: date, in_range: true, status: 'ok', on: '6:59am', off: '3:54pm',
  on_exact: '06:59', off_exact: '15:54', worked_label: '8h 24m', leave: [], ...extra });
const view = {
  current: { name: 'Test Person', team: 'AKL Operations', days: [day('2026-10-07', { status: 'today_pending' }), day('2026-10-08', { status: 'future', in_range: false })] },
  previous: { name: 'Test Person', team: 'AKL Operations', days: [day('2026-09-30'), day('2026-09-26', { status: 'no_entry', on: null, off: null, on_exact: null, off_exact: null })] },
};
const today = '2026-10-07';
const ok = (b, c = cfg) => validate(b, view, c, today);

test('valid request', () => {
  const r = ok({ date: '2026-09-30', start: '07:00', finish: '16:30', reason: 'forgot to clock off' });
  assert.equal(r.ok, true); assert.equal(r.span, 570); assert.equal(r.found.which, 'previous');
});
test('no-entry day allowed', () => assert.equal(ok({ date: '2026-09-26', start: '07:00', finish: '12:00' }).ok, true));
test('future and out-of-range days rejected', () => {
  assert.equal(ok({ date: '2026-10-08', start: '07:00', finish: '15:00' }).code, 'bad_date');
  assert.equal(ok({ date: '2026-09-01', start: '07:00', finish: '15:00' }).code, 'bad_date');
  assert.equal(ok({ date: 'x', start: '07:00', finish: '15:00' }).code, 'bad_date');
});
test('previous fortnight can be switched off', () => {
  const c = { ...cfg, rules: { ...cfg.rules, allow_previous_fortnight: false } };
  assert.equal(ok({ date: '2026-09-30', start: '07:00', finish: '15:00' }, c).code, 'bad_date');
});
test('time checks', () => {
  assert.equal(ok({ date: '2026-09-30', start: '7:00', finish: '15:00' }).code, 'bad_time');
  assert.equal(ok({ date: '2026-09-30', start: '15:00', finish: '07:00' }).code, 'order');
  assert.equal(ok({ date: '2026-09-30', start: '00:30', finish: '23:30' }).code, 'too_long');
  assert.equal(ok({ date: '2026-09-30', start: '06:59', finish: '15:54' }).code, 'same');
  assert.equal(ok({ date: '2026-09-30', start: '07:00', finish: '16:00', reason: 'x'.repeat(501) }).code, 'reason_long');
});
test('15-minute step rule when configured', () => {
  const c = { ...cfg, rules: { ...cfg.rules, time_step_minutes: 15 } };
  assert.equal(ok({ date: '2026-09-30', start: '07:05', finish: '16:00' }, c).code, 'step');
  assert.equal(ok({ date: '2026-09-30', start: '07:15', finish: '16:00' }, c).ok, true);
});
test('disabled', () => assert.equal(ok({ date: '2026-09-30', start: '07:00', finish: '16:00' }, { ...cfg, enabled: false }).code, 'disabled'));
test('routing incl. Dan cover until 16 Oct', () => {
  assert.equal(routeManager('AKL Operations', cfg, today).to, 'Greg@inspirekitchens.co.nz');
  assert.equal(routeManager('AKL Sales', cfg, today).to, 'aaron@inspirekitchens.co.nz');
  assert.equal(routeManager('QT Ops', cfg, today).to, 'qtops@inspirekitchens.co.nz');
  assert.equal(routeManager('QT Sales + Dunc', cfg, '2026-10-16').to, 'aaron@inspirekitchens.co.nz');
  assert.equal(routeManager('QT Sales + Dunc', cfg, '2026-10-17').to, 'Dan@inspirekitchens.co.nz');
  assert.equal(routeManager('Nope', cfg, today).to, cfg.fallback_to);
});
test('email escapes HTML and has no em dashes', () => {
  const r = ok({ date: '2026-09-30', start: '07:00', finish: '16:30', reason: '<script>x</script> & stuff' });
  const e = buildEmail({ staff: r.found.staff, day: r.found.day, req: r, ref: 'TS-1', submittedLabel: 'Wed 7 Oct 2026, 3:42pm', route: { to: 'a', why: '' } });
  assert.ok(!e.html.includes('<script>')); assert.ok(e.html.includes('&lt;script&gt;'));
  assert.ok(!/\u2014/.test(e.html + e.text + e.subject));
  assert.match(e.text, /Requested start: 7:00am/); assert.match(e.text, /Requested finish: 4:30pm/);
  assert.match(e.text, /In Jobman now: 6:59am to 3:54pm \(8h 24m logged\)/);
});
test('email shows when counted time started', () => {
  const v2 = JSON.parse(JSON.stringify(view));
  Object.assign(v2.previous.days[0], { on: '6:35am', on_exact: '06:35', counted_from: '7:00am', counted_from_exact: '07:00', counted_from_source: 'first_task' });
  const r = validate({ date: '2026-09-30', start: '06:35', finish: '16:00' }, v2, cfg, today);
  const e = buildEmail({ staff: r.found.staff, day: r.found.day, req: r, ref: 'TS-2', submittedLabel: 'x', route: { to: 'a', why: '' } });
  assert.match(e.text, /Hours count from 7:00am \(first task started\)/);
});
