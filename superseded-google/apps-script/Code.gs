/**
 * Inspire Kitchens: Staff Timesheet View (VIEW ONLY, MVP)
 *
 * A mobile web page where each staff member sees their own daily timesheet and the
 * fortnight-to-date running total. Jobman stays the only clock; this never writes anywhere.
 *
 * Deploy: Deploy > New deployment > Web app, Execute as: Me, Who has access: Anyone.
 * Each person's link is  <exec URL>?t=<their secret token>.
 *
 * Data: a PRIVATE Drive folder (Script property DATA_FOLDER_ID) holding files uploaded by the
 * nightly routine on the box:
 *   stv_bundle_<periodStart>_<yyyymmddHHMM>.json   staff-facing data for one fortnight
 *   stv_tokens_<yyyymmddHHMM>.json                 SHA-256 token hashes + staff_id + status
 * The newest file of each kind wins. Raw tokens are never stored in Google.
 *
 * Script properties (Project Settings > Script properties):
 *   DATA_FOLDER_ID          required  Drive folder id of the private data folder
 *   REVOKED_STAFF_IDS       optional  comma-separated staff ids blocked at once (adminRevokeStaff)
 *   SHOW_CHANGE_REQUESTS    optional  "true" shows the (phase 2) Request a change button. Default off.
 *   DISPLAY_SNAP            optional  "true" shows clocks snapped to 15 minutes. Default exact times.
 *
 * Scope is drive.readonly on purpose: the page faces the internet and runs as the owner, so it
 * can read the data folder but cannot change or delete anything. Old files are tidied by the routine.
 */

var CACHE_TTL_S = 300;          // how long a parsed bundle / token list is cached (5 min)
var TOKEN_RE = /^[A-Za-z0-9_-]{32,64}$/;
var BUNDLE_PREFIX = 'stv_bundle_';
var TOKENS_PREFIX = 'stv_tokens_';

function doGet(e) {
  var token = (e && e.parameter && e.parameter.t) ? String(e.parameter.t) : '';
  try {
    var staffId = staffIdForToken_(token);
    if (!staffId) return errorPage_();
    var current = staffDataFor_(staffId, 'current');
    var previous = staffDataFor_(staffId, 'previous');
    if (!current && !previous) return notReadyPage_();
    var payload = {
      current: publicView_(current),
      previous: publicView_(previous),
      features: {
        changeRequests: prop_('SHOW_CHANGE_REQUESTS') === 'true',
        displaySnap: prop_('DISPLAY_SNAP') === 'true'
      }
    };
    var tpl = HtmlService.createTemplateFromFile('Page');
    tpl.payloadJson = safeJson_(payload);
    return tpl.evaluate()
      .setTitle('My timesheet')
      .addMetaTag('viewport', 'width=device-width, initial-scale=1')
      .addMetaTag('robots', 'noindex, nofollow')
      .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.DEFAULT);
  } catch (err) {
    console.error('doGet failed: ' + (err && err.message));   // never log the token
    return errorPage_();
  }
}

/** Phase 2 placeholder: change requests are not switched on in the MVP. */
function doPost(e) {
  return ContentService.createTextOutput(JSON.stringify({ ok: false, message: 'Not available yet.' }))
    .setMimeType(ContentService.MimeType.JSON);
}

// ---------------------------------------------------------------- tokens

function staffIdForToken_(token) {
  if (!TOKEN_RE.test(token)) { noteFailure_(); return null; }
  var hash = sha256Hex_(token);
  var map = tokenMap_();
  var entry = map[hash];
  if (!entry || entry.status !== 'active') { noteFailure_(); return null; }
  var revoked = (prop_('REVOKED_STAFF_IDS') || '').split(',').map(function (s) { return s.trim(); });
  if (revoked.indexOf(entry.staff_id) >= 0) { noteFailure_(); return null; }
  return entry.staff_id;
}

function tokenMap_() {
  var file = latestFile_(TOKENS_PREFIX);
  if (!file) return {};
  var key = 'tok:' + file.getId();
  var cache = CacheService.getScriptCache();
  var hit = cache.get(key);
  if (hit) return JSON.parse(hit);
  var data = JSON.parse(file.getBlob().getDataAsString('UTF-8'));
  var map = {};
  (data.tokens || []).forEach(function (t) { map[t.token_hash] = { staff_id: t.staff_id, status: t.status }; });
  cache.put(key, JSON.stringify(map), CACHE_TTL_S);
  return map;
}

function sha256Hex_(s) {
  var bytes = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, s, Utilities.Charset.UTF_8);
  return bytes.map(function (b) { var v = (b < 0 ? b + 256 : b).toString(16); return v.length === 1 ? '0' + v : v; }).join('');
}

/** Count bad links for monitoring (no token or staff detail is stored). */
function noteFailure_() {
  var cache = CacheService.getScriptCache();
  var n = Number(cache.get('fail_count') || '0') + 1;
  cache.put('fail_count', String(n), 3600);
  if (n === 50 || n === 200) console.warn('Staff Timesheet View: ' + n + ' bad link attempts in the last hour');
}

// ---------------------------------------------------------------- data

/** which: 'current' = fortnight containing today (NZ), 'previous' = the one before. */
function staffDataFor_(staffId, which) {
  var startIso = fortnightStartIso_(new Date(), which === 'previous' ? -1 : 0);
  var file = latestFile_(BUNDLE_PREFIX + startIso + '_');
  if (!file) return null;
  var cache = CacheService.getScriptCache();
  var key = 'b:' + file.getId() + ':' + staffId;
  var hit = cache.get(key);
  if (hit) return hit === '-' ? null : JSON.parse(hit);
  var bundle = JSON.parse(file.getBlob().getDataAsString('UTF-8'));
  if (bundle.kind !== 'staff-timesheet-view-bundle' || bundle.schema !== 1) return null;
  var puts = {};
  Object.keys(bundle.staff || {}).forEach(function (sid) {
    var s = JSON.stringify(bundle.staff[sid]);
    if (s.length < 90000) puts['b:' + file.getId() + ':' + sid] = s;
  });
  cache.putAll(puts, CACHE_TTL_S);
  var mine = bundle.staff ? bundle.staff[staffId] : null;
  if (!mine) cache.put(key, '-', CACHE_TTL_S);
  return mine || null;
}

/** Strip anything the page does not need (ids stay server side). */
function publicView_(s) {
  if (!s) return null;
  var copy = JSON.parse(JSON.stringify(s));
  delete copy.staff_id;
  return copy;
}

/** Pay fortnights run Wednesday to Tuesday; 23 Sep 2026 is a fortnight Wednesday.
 *  Returns the YYYY-MM-DD start of the fortnight containing today in NZ, shifted by `shift` fortnights. */
function fortnightStartIso_(now, shift) {
  var todayIso = Utilities.formatDate(now, 'Pacific/Auckland', 'yyyy-MM-dd');
  var anchor = isoToUtcDate_('2026-09-23');
  var days = Math.round((isoToUtcDate_(todayIso) - anchor) / 86400000);
  var k = Math.floor(days / 14) + (shift || 0);
  return new Date(anchor.getTime() + k * 14 * 86400000).toISOString().slice(0, 10);
}

function isoToUtcDate_(iso) {
  var p = iso.split('-');
  return new Date(Date.UTC(Number(p[0]), Number(p[1]) - 1, Number(p[2])));
}

/** Newest file in the data folder whose name starts with prefix (names carry a sortable stamp). */
function latestFile_(prefix) {
  var cache = CacheService.getScriptCache();
  var ck = 'latest:' + prefix;
  var id = cache.get(ck);
  if (id) {
    try { return DriveApp.getFileById(id); } catch (e) { /* fall through */ }
  }
  var folderId = prop_('DATA_FOLDER_ID');
  if (!folderId) throw new Error('DATA_FOLDER_ID not set');
  var it = DriveApp.getFolderById(folderId).getFiles();
  var best = null, bestName = '';
  while (it.hasNext()) {
    var f = it.next();
    var n = f.getName();
    if (n.indexOf(prefix) === 0 && /\.json$/.test(n) && !f.isTrashed() && n > bestName) { best = f; bestName = n; }
  }
  if (best) cache.put(ck, best.getId(), 120);
  return best;
}

// ---------------------------------------------------------------- pages

function errorPage_() {
  return simplePage_('This link is not working',
    'Please ask your manager for a new link to your timesheet.');
}

function notReadyPage_() {
  return simplePage_('Your timesheet is not ready yet',
    'Please check again tomorrow. If it still does not show, speak to your manager.');
}

function simplePage_(title, body) {
  var t = HtmlService.createTemplateFromFile('Message');
  t.title = title;
  t.body = body;
  return t.evaluate().setTitle('My timesheet')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1')
    .addMetaTag('robots', 'noindex, nofollow');
}

/** JSON that is safe to drop inside a <script> tag. */
function safeJson_(obj) {
  return JSON.stringify(obj)
    .replace(/</g, '\\u003c').replace(/>/g, '\\u003e').replace(/&/g, '\\u0026')
    .replace(/\u2028/g, '\\u2028').replace(/\u2029/g, '\\u2029');
}

function prop_(k) { return PropertiesService.getScriptProperties().getProperty(k); }

// ---------------------------------------------------------------- admin (run from the editor)

/** Quick self-test: checks the folder, the newest files and counts. Logs no tokens. */
function adminCheckSetup() {
  var tok = latestFile_(TOKENS_PREFIX);
  var curIso = fortnightStartIso_(new Date(), 0);
  var b = latestFile_(BUNDLE_PREFIX + curIso + '_');
  var map = tokenMap_();
  console.log('Tokens file: ' + (tok ? tok.getName() : 'MISSING') + ', active hashes: ' +
    Object.keys(map).filter(function (h) { return map[h].status === 'active'; }).length);
  console.log('Current fortnight starts ' + curIso + ', bundle: ' + (b ? b.getName() : 'MISSING'));
}

/** Instantly block one person's link(s) without waiting for a new tokens file. */
function adminRevokeStaff(staffId) {
  if (!staffId) throw new Error('Pass a staff id');
  var p = PropertiesService.getScriptProperties();
  var list = (p.getProperty('REVOKED_STAFF_IDS') || '').split(',').filter(String);
  if (list.indexOf(staffId) < 0) list.push(staffId);
  p.setProperty('REVOKED_STAFF_IDS', list.join(','));
  console.log('Blocked ' + staffId + '. Reissue a token on the box to give them a new link, then remove the block.');
}

function adminUnblockStaff(staffId) {
  var p = PropertiesService.getScriptProperties();
  var list = (p.getProperty('REVOKED_STAFF_IDS') || '').split(',').filter(function (s) { return s && s !== staffId; });
  p.setProperty('REVOKED_STAFF_IDS', list.join(','));
}
