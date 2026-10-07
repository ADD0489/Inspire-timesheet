// Offline smoke test of Code.gs with stubbed Apps Script services (no Google calls).
const fs = require('fs'), path = require('path'), crypto = require('crypto'), vm = require('vm');
const ROOT = path.resolve(__dirname, '..', '..');
const UP = path.join(ROOT, 'out', 'upload');
const files = fs.readdirSync(UP).filter(n => n.startsWith('stv_')).map((n, i) => ({
  id: 'f' + i, name: n, data: fs.readFileSync(path.join(UP, n), 'utf8')
}));
const props = { DATA_FOLDER_ID: 'folder1' };
const cacheStore = {};
const fakeNow = process.env.FAKE_NOW || '2026-10-07T07:30:00Z';
const RealDate = Date;
class FakeDate extends RealDate { constructor(...a) { if (a.length === 0) super(fakeNow); else super(...a); } static now() { return new RealDate(fakeNow).getTime(); } }
let lastTemplate = null;
const ctx = {
  Date: FakeDate, JSON, Math, Number, String, Object, Array, RegExp, console,
  Utilities: {
    DigestAlgorithm: { SHA_256: 'sha256' }, Charset: { UTF_8: 'utf8' },
    computeDigest: (alg, s) => Array.from(crypto.createHash('sha256').update(s, 'utf8').digest()).map(b => b > 127 ? b - 256 : b),
    formatDate: (d, tz, fmt) => new Intl.DateTimeFormat('en-CA', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit' }).format(d),
  },
  PropertiesService: { getScriptProperties: () => ({ getProperty: k => props[k] || null, setProperty: (k, v) => { props[k] = v; } }) },
  CacheService: { getScriptCache: () => ({ get: k => cacheStore[k] || null, put: (k, v) => { cacheStore[k] = v; }, putAll: o => Object.assign(cacheStore, o) }) },
  DriveApp: {
    getFileById: id => wrap(files.find(f => f.id === id)),
    getFolderById: () => ({ getFiles: () => { let i = 0; return { hasNext: () => i < files.length, next: () => wrap(files[i++]) }; } }),
  },
  HtmlService: {
    XFrameOptionsMode: { DEFAULT: 'DEFAULT' },
    createTemplateFromFile: name => {
      const t = { name };
      t.evaluate = () => {
        let html = fs.readFileSync(path.join(ROOT, 'apps-script', name + '.html'), 'utf8');
        html = html.replace('<?!= payloadJson ?>', t.payloadJson || '').replace('<?= title ?>', t.title || '').replace('<?= body ?>', t.body || '');
        lastTemplate = { name, html, t };
        const out = { html, setTitle: () => out, addMetaTag: () => out, setXFrameOptionsMode: () => out };
        return out;
      };
      return t;
    },
  },
  ContentService: { createTextOutput: s => ({ setMimeType: () => s }), MimeType: { JSON: 'json' } },
};
function wrap(f) { if (!f) throw new Error('no file'); return { getId: () => f.id, getName: () => f.name, isTrashed: () => false, getBlob: () => ({ getDataAsString: () => f.data }) }; }
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(ROOT, 'apps-script', 'Code.gs'), 'utf8'), ctx);

const store = JSON.parse(fs.readFileSync(path.join(ROOT, 'secrets', 'stv_tokens.json'), 'utf8'));
const ashton = store.tokens.find(t => t.name === 'Ashton Pienaar' && t.status === 'active');
function run(label, t) {
  const out = ctx.doGet({ parameter: t === undefined ? {} : { t } });
  const m = out.html.match(/var STV = (.*);\n/);
  const payload = m ? JSON.parse(m[1]) : null;
  const tokenLeak = t && out.html.includes(t);
  const otherNames = payload ? ['Duncan', 'Clebson', 'Madi', 'Gladys'].filter(n => out.html.includes(n)) : [];
  console.log(label.padEnd(28), lastTemplate.name.padEnd(8),
    payload ? `cur=${payload.current && payload.current.period.label} prev=${payload.previous && payload.previous.period.label} name=${(payload.current || payload.previous).name} staff_id_in_page=${out.html.includes(ashton.staff_id)}` : out.html.match(/<h1>(.*)<\/h1>/)[1],
    `token_in_page=${!!tokenLeak} other_staff_in_page=${otherNames.length}`);
}
run('valid token (7 Oct)', ashton.token);
run('no token', undefined);
run('garbage', 'abc');
run('well-formed wrong token', 'A'.repeat(32));
ctx.adminRevokeStaff(ashton.staff_id);
run('after adminRevokeStaff', ashton.token);
ctx.adminUnblockStaff(ashton.staff_id);
run('after unblock', ashton.token);
for (const [lbl, iso] of [['7 Oct 7:30pm NZ', '2026-10-07T06:30:00Z'], ['6 Oct 11pm NZ', '2026-10-06T10:00:00Z'], ['7 Oct 0:30am NZ', '2026-10-06T11:30:00Z'], ['20 Oct 11pm NZ', '2026-10-20T10:00:00Z'], ['21 Oct 9am NZ', '2026-10-20T20:00:00Z'], ['22 Sep 2026 NZ', '2026-09-22T01:00:00Z'], ['5 Apr 2027 (after DST end)', '2027-04-05T01:00:00Z']]) {
  console.log(('fortnight for ' + lbl).padEnd(44), ctx.fortnightStartIso_(new RealDate(iso), 0), 'prev', ctx.fortnightStartIso_(new RealDate(iso), -1));
}
