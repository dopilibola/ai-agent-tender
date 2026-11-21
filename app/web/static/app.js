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
    window.location.href = tr.getAttribute('data-href');
  });
  document.addEventListener('DOMContentLoaded', update);
})();
