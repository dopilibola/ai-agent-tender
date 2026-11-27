// Theme lives in a server cookie (works without JS). JS only tells the 🌙/☀️ toggle what "auto" looks like now.
document.addEventListener('DOMContentLoaded', function () {
  var dark = window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches;
  document.querySelectorAll('input[name="cur"]').forEach(function (i) {
    if (i.value === 'auto') i.value = dark ? 'dark' : 'light';
  });
});
// CRM table behaviour: select rows (bulk bar), select all, click a row to open it.
(function () {
  function update() {
    var boxes = document.querySelectorAll('tbody input[name="ids"]');
    var n = 0;
    boxes.forEach(function (b) { b.closest('tr').classList.toggle('sel', b.checked); if (b.checked) n++; });
    var bar = document.getElementById('bulk');
    if (bar) { bar.classList.toggle('show', n > 0); var c = bar.querySelector('[data-count]'); if (c) c.textContent = n; }
    var all = document.querySelector('[data-all]');
    if (all) { all.checked = n > 0 && n === boxes.length; all.indeterminate = n > 0 && n < boxes.length; }
  }
  document.addEventListener('change', function (e) {
    if (e.target.matches('[data-all]')) {
      document.querySelectorAll('tbody input[name="ids"]').forEach(function (b) { b.checked = e.target.checked; });
    }
    if (e.target.matches('input[type=checkbox]')) update();
  });
  document.addEventListener('click', function (e) {
    var tr = e.target.closest('tr[data-href]');
    if (!tr || e.target.closest('a,input,button,label,.cb')) return;
    if (window.getSelection && String(window.getSelection())) return;  // let people copy text
    if (tr.hasAttribute('data-ext')) { window.open(tr.getAttribute('data-href'), '_blank', 'noopener,noreferrer'); return; }
    window.location.href = tr.getAttribute('data-href');
  });
  document.addEventListener('DOMContentLoaded', update);
})();
// 🤖 AI chat: send without reloading, show the question at once and the answer when it comes.
(function () {
  function log() { return document.getElementById('chatlog'); }
  function bubble(cls, text) {
    var d = document.createElement('div');
    d.className = 'msg ' + cls;
    d.textContent = text;
    log().appendChild(d);
    d.scrollIntoView({ block: 'end', behavior: 'smooth' });
    return d;
  }
  var busy = false;
  document.addEventListener('submit', function (e) {
    var form = e.target.closest('form[data-chat]');
    if (!form || !log() || !window.fetch) return;
    e.preventDefault();
    if (busy) return;
    var data = new FormData(form);
    if (e.submitter && e.submitter.name) data.set(e.submitter.name, e.submitter.value);
    var q = String(data.get('q') || '').trim();
    if (!q) return;
    busy = true;
    document.querySelectorAll('form[data-chat] button, form[data-chat] textarea').forEach(function (x) { x.disabled = true; });
    bubble('user', q);
    var wait = bubble('ai wait', 'AI hujjatlarni o‘qib javob yozmoqda');
    var ta = document.querySelector('form.ask textarea');
    if (ta) ta.value = '';
    fetch(form.action, { method: 'POST', body: data, credentials: 'same-origin',
                         headers: { 'X-Requested-With': 'fetch' } })
      .then(function (r) { return r.json().catch(function () { return { ok: false, error: 'Server javobi tushunarsiz (' + r.status + ')' }; }); })
      .then(function (j) {
        if (j.ok) {
          var t = document.createElement('template');
          t.innerHTML = j.html;
          wait.replaceWith(t.content.firstChild);
        } else {
          wait.className = 'msg err';
          wait.textContent = j.error || 'Xato';
        }
      })
      .catch(function () { wait.className = 'msg err'; wait.textContent = 'Internet yoki server bilan aloqa uzildi.'; })
      .then(function () {
        busy = false;
        document.querySelectorAll('form[data-chat] button, form[data-chat] textarea').forEach(function (x) { x.disabled = false; });
        var last = log().lastElementChild;
        if (last) last.scrollIntoView({ block: 'end' });
        if (ta) ta.focus();
      });
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.target.matches('form.ask textarea')) {
      e.preventDefault();
      e.target.form.requestSubmit();
    }
  });
  document.addEventListener('DOMContentLoaded', function () {
    var l = log();
    if (l && l.lastElementChild) l.lastElementChild.scrollIntoView({ block: 'end' });
  });
})();
