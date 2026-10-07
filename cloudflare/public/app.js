// Staff Timesheet View, simplified page (v2): logged hours by day, running total and fortnight total,
// plus "Request a change". The link token is read from the URL fragment (#t=...), which browsers never
// send to the server, and is sent only in the Authorization header of POST requests.
(function () {
  'use strict';
  var STV = null, view = 'current', token = null;
  var F = { changeRequests: false, timeStepMinutes: 1, maxPerDay: 5, allowPreviousFortnight: true, reasonMaxChars: 500, today: '' };
  var $ = function (id) { return document.getElementById(id); };

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  function plural(n, one, many) { return n + ' ' + (n === 1 ? one : many); }
  function to12(t) {
    if (!t) return '';
    var p = t.split(':'), h = +p[0], m = p[1];
    return (h % 12 === 0 ? 12 : h % 12) + ':' + m + (h >= 12 ? 'pm' : 'am');
  }
  function mins(t) { var p = t.split(':'); return (+p[0]) * 60 + (+p[1]); }

  function readToken() {
    var m = /(?:^#|[#&])t=([A-Za-z0-9_-]{20,128})/.exec(location.hash || '');
    if (m) return m[1];
    var q = /[?&]t=([A-Za-z0-9_-]{20,128})/.exec(location.search || '');
    if (q) {
      // Move an old-style ?t= link into the fragment so it is not kept in history or sent again
      try { history.replaceState(null, '', location.pathname + '#t=' + q[1]); } catch (e) { /* ignore */ }
      return q[1];
    }
    return null;
  }

  function showMessage(title, text) {
    $('main').hidden = true;
    var box = $('message');
    box.innerHTML = '';
    box.appendChild(el('h2', '', title));
    box.appendChild(el('p', 'empty', text));
    box.hidden = false;
  }

  function api(path, body) {
    return fetch(path, {
      method: 'POST', cache: 'no-store', credentials: 'omit',
      headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) { j._status = r.status; return j; });
    });
  }

  function canRequest(d, which) {
    if (!F.changeRequests || !d.in_range || d.status === 'future') return false;
    if (F.today && d.date > F.today) return false;
    if (which === 'previous' && !F.allowPreviousFortnight) return false;
    return true;
  }

  function render() {
    var s = STV[view];
    $('tabs').hidden = !(STV.current && STV.previous);
    $('tab-current').setAttribute('aria-pressed', view === 'current');
    $('tab-previous').setAttribute('aria-pressed', view === 'previous');
    var card = $('total-card'), list = $('days');
    card.innerHTML = ''; list.innerHTML = '';
    $('req-open').hidden = !F.changeRequests;
    $('foot-help').textContent = F.changeRequests ? 'Send a change request below, or speak to your manager.' : 'Please speak to your manager.';
    if (!s) { card.appendChild(el('div', 'empty', 'Nothing to show for this fortnight yet.')); return; }

    $('hello').textContent = 'Kia ora ' + s.first_name;
    $('period').textContent = 'Pay fortnight: ' + s.period.label;
    $('updated').textContent = 'Last updated ' + s.updated_label + ' (NZ time)';

    var t = s.totals;
    var finished = s.range.to >= s.period.end;
    card.appendChild(el('div', 'total-label', finished ? 'Your logged hours for the fortnight' : 'Your logged hours so far this fortnight'));
    card.appendChild(el('div', 'total', t.total_label));
    if (t.leave_h > 0) card.appendChild(el('div', 'total-sub', 'Includes ' + t.leave_label + ' of leave'));
    if (t.fortnight_total_being_checked) {
      card.appendChild(el('div', 'notice', 'Your fortnight total is being double-checked. Some days are not included yet.'));
    } else if (t.being_checked_days_not_in_total > 0) {
      card.appendChild(el('div', 'notice', plural(t.being_checked_days_not_in_total, 'day is', 'days are') + ' being checked and not in your total yet.'));
    } else if (t.being_checked_days > 0) {
      card.appendChild(el('div', 'notice', plural(t.being_checked_days, 'day is', 'days are') + ' being checked. The hours are included.'));
    }
    if (t.from_clock_times) {
      card.appendChild(el('div', 'info', 'Hours are worked out from your Jobman clock times and may change a little once your manager has checked them.'));
    }

    s.days.forEach(function (d) {
      var li = el('li', 'day' + (d.is_weekend ? ' weekend' : '') + (d.status === 'future' ? ' future' : ''));
      var left = el('div', 'd-left'), right = el('div', 'd-right');
      left.appendChild(el('div', 'd-date', d.day_label));
      var reqInNote = false;
      if (d.status === 'ok' && d.on && d.off) {
        left.appendChild(el('div', 'd-clock', d.on + ' to ' + d.off));
        if (d.counted_from) {
          // Jobman paid-start: shift_start/start_rule = no task before shift; first_task = task before shift but after clock
          var why = d.counted_from_source === 'first_task' ? 'when your first task started' : 'the usual start time';
          var n = el('div', 'd-note');
          var lead = 'Clocked on ' + d.on + '; hours count from ' + d.counted_from + ' (' + why + ').';
          if (d.counted_from_source === 'first_task') {
            n.appendChild(document.createTextNode(lead + ' '));
          } else {
            n.appendChild(document.createTextNode(lead + ' Forgot to start a task? '));
          }
          if (canRequest(d, view)) {
            var rl = el('button', 'req-inline', 'Request a change'); rl.type = 'button';
            rl.onclick = function () { openSheet(d.date); };
            n.appendChild(rl);
            reqInNote = true;
          } else if (d.counted_from_source !== 'first_task') {
            n.appendChild(document.createTextNode('Please tell your manager.'));
          }
          left.appendChild(n);
        }
      } else if (d.status === 'being_checked') {
        left.appendChild(el('span', 'badge check', 'Being checked'));
        if (d.note) left.appendChild(el('div', 'd-sub', d.note));
      } else if (d.status === 'future') {
        left.appendChild(el('div', 'd-sub', 'Coming up'));
      } else if (d.status === 'today_pending') {
        left.appendChild(el('div', 'd-sub', d.note || "Today's hours show here after you clock off."));
      } else if (d.status === 'no_entry') {
        left.appendChild(el('div', 'd-sub', d.is_weekend ? 'Weekend' : 'No clock entry'));
      }
      (d.leave || []).forEach(function (l) { left.appendChild(el('span', 'badge leave', l.type + ' ' + l.label)); });
      var hasEntry = d.status === 'ok' || d.status === 'being_checked' || (d.status === 'no_entry' && !d.is_weekend);
      if (hasEntry && !reqInNote && canRequest(d, view)) {
        var b = el('button', 'req', 'Request a change'); b.type = 'button';
        b.onclick = function () { openSheet(d.date); };
        left.appendChild(b);
      }
      if (d.worked_label) right.appendChild(el('div', 'd-hours', d.worked_label));
      else if (d.status === 'being_checked') right.appendChild(el('div', 'd-hours', '--'));
      if (d.running_total_label && d.in_range && (d.worked_label || (d.leave && d.leave.length))) right.appendChild(el('div', 'd-run', 'Total ' + d.running_total_label));
      li.appendChild(left); li.appendChild(right); list.appendChild(li);
    });
  }

  // ---------- Request a change ----------
  var choices = [];
  function eligibleDays() {
    var out = [];
    ['current', 'previous'].forEach(function (w) {
      var s = STV[w];
      if (!s) return;
      s.days.forEach(function (d) { if (canRequest(d, w)) out.push(d); });
    });
    out.sort(function (a, b) { return a.date < b.date ? 1 : -1; });
    return out;
  }
  function jobmanNow(d) {
    if (!d) return '';
    var times = d.on && d.off ? d.on + ' to ' + d.off : '';
    var txt;
    if (d.status === 'ok') txt = times ? times + ' (' + (d.worked_label || 'no hours') + ')' : 'Hours logged, no clock times';
    if (d.status === 'ok' && d.counted_from) txt += '; hours count from ' + d.counted_from;
    else if (d.status === 'being_checked') txt = (times ? times + ', ' : '') + 'being checked';
    else if (d.status === 'today_pending') txt = times ? times + ' (not finalised yet)' : 'Not finalised yet';
    else txt = 'No clock entry';
    return 'Jobman has: ' + txt;
  }
  function selectedDay() {
    var v = $('req-date').value;
    for (var i = 0; i < choices.length; i++) if (choices[i].date === v) return choices[i];
    return null;
  }
  function onDayChange() {
    var d = selectedDay();
    $('req-now').textContent = jobmanNow(d);
    $('req-start').value = (d && d.on_exact) || '';
    $('req-finish').value = (d && d.off_exact) || '';
    $('req-error').hidden = true;
  }
  function openSheet(date) {
    choices = eligibleDays();
    var sel = $('req-date');
    sel.innerHTML = '';
    choices.forEach(function (d) {
      var o = el('option', '', d.day_label + (d.status === 'no_entry' ? ' (no clock entry)' : ''));
      o.value = d.date; sel.appendChild(o);
    });
    if (date) sel.value = date;
    var step = Math.max(1, F.timeStepMinutes) * 60;
    $('req-start').step = step; $('req-finish').step = step;
    $('req-reason').maxLength = F.reasonMaxChars;
    $('req-reason').value = '';
    $('req-limit').textContent = 'Your request is emailed to your manager. You can send up to ' + F.maxPerDay + ' a day.';
    $('req-form').hidden = false; $('req-done').hidden = true;
    $('req-send').disabled = false; $('req-send').textContent = 'Send request';
    onDayChange();
    $('sheet').hidden = false;
    document.body.style.overflow = 'hidden';
  }
  function closeSheet() { $('sheet').hidden = true; document.body.style.overflow = ''; }
  function showError(msg) { var e = $('req-error'); e.textContent = msg; e.hidden = false; }

  function submit(ev) {
    ev.preventDefault();
    var d = selectedDay(), start = $('req-start').value, finish = $('req-finish').value;
    var reason = $('req-reason').value.trim();
    if (!d) return showError('Please choose a day.');
    if (!/^\d{2}:\d{2}$/.test(start) || !/^\d{2}:\d{2}$/.test(finish)) return showError('Please enter both a start time and a finish time.');
    if (F.timeStepMinutes > 1 && (mins(start) % F.timeStepMinutes || mins(finish) % F.timeStepMinutes)) {
      return showError('Please use times in ' + F.timeStepMinutes + '-minute steps, for example 7:00, 7:15 or 7:30.');
    }
    if (mins(finish) <= mins(start)) return showError('The finish time needs to be after the start time.');
    if (d.on_exact === start && d.off_exact === finish) return showError('Those times are the same as Jobman already has for that day.');
    $('req-send').disabled = true; $('req-send').textContent = 'Sending...';
    var payload = { date: d.date, start: start, finish: finish, reason: reason };
    var p = window.STV_PREVIEW ? Promise.resolve({ _status: 200, ok: true, ref: 'TS-PREVIEW', remaining_today: F.maxPerDay - 1 }) : api('/api/request', payload);
    p.then(function (r) {
      if (r.ok) {
        $('req-form').hidden = true; $('req-done').hidden = false;
        $('req-done-text').textContent = 'Your manager has been emailed about ' + d.day_label + ': ' + to12(start) + ' to ' + to12(finish) + '.';
        $('req-done-ref').textContent = 'Reference ' + r.ref + '. You can send ' + plural(r.remaining_today, 'more request', 'more requests') + ' today.';
      } else if (r._status === 404) {
        showError('This link is no longer working. Please speak to your manager.');
      } else {
        showError(r.message || 'Sorry, your request could not be sent just now. Please try again later, or speak to your manager.');
      }
    }).catch(function () {
      showError('Could not connect. Please check your signal and try again.');
    }).then(function () {
      $('req-send').disabled = false; $('req-send').textContent = 'Send request';
    });
  }

  $('tab-current').onclick = function () { view = 'current'; render(); };
  $('tab-previous').onclick = function () { view = 'previous'; render(); };
  $('req-open').onclick = function () { openSheet(null); };
  $('req-cancel').onclick = closeSheet;
  $('req-close').onclick = closeSheet;
  $('req-date').onchange = onDayChange;
  $('req-form').onsubmit = submit;
  $('sheet').onclick = function (e) { if (e.target === $('sheet')) closeSheet(); };

  function start(data) {
    STV = data;
    if (data.features) for (var k in data.features) F[k] = data.features[k];
    view = STV.current ? 'current' : 'previous';
    $('message').hidden = true; $('main').hidden = false;
    render();
  }

  if (window.STV_PREVIEW) { start(window.STV_PREVIEW); return; }
  token = readToken();
  if (!token) { showMessage('This link is not complete', 'Please open the full link your manager sent you.'); return; }
  showMessage('Loading', 'Getting your hours from Jobman...');
  api('/api/view').then(function (r) {
    if (r._status === 404) return showMessage('This link is not working', 'It may have been replaced with a new one. Please ask your manager for your current link.');
    if (r.status === 'not_ready') return showMessage('Not ready yet', 'Your timesheet will show here after the next nightly update.');
    if (r.status !== 'ok') return showMessage('Something went wrong', 'Please try again in a few minutes.');
    start(r);
  }).catch(function () {
    showMessage('Could not connect', 'Please check your signal and try again.');
  });
})();
