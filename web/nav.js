/* Shared top navigation for the TdF Manager dashboard.
   Renders into <nav class="page-nav" id="mainNav"></nav> and marks the
   active entry from the current filename. Single source of truth — edit
   here, every page updates. */
(function () {
  var NAV = [
    { href: 'vuelta.html', label: 'Vuelta 2026', match: ['vuelta.html', ''] },
    { href: 'evaluering.html', label: 'Analyse', match: ['evaluering.html', 'laer-af-touren.html'] },
    { href: 'riders.html',    label: 'Rytterdatabase', match: ['riders.html'] },
    { href: 'analytics.html', label: 'ML-Analyse',     match: ['analytics.html'] },
    { href: 'model.html',     label: 'Modelforklaring', match: ['model.html'] },
    { label: 'Arkiv', alignRight: true, match: ['index.html', 'giro.html', 'dauphine.html'], menu: [
        { href: 'index.html',    label: 'Tour de France 2026' },
        { href: 'giro.html',     label: "Giro d'Italia 2026" },
        { href: 'dauphine.html', label: 'Dauphiné 2026' }
    ] }
  ];

  // Self-contained nav styles — injected once so the nav renders identically
  // on every page, including analytics.html which does not load style.css.
  var CSS = [
    '.page-nav{display:flex;gap:4px;align-items:center;flex-wrap:wrap}',
    '.page-nav .nav-link{display:inline-block;color:#7B82A0;background:none;border:1px solid #2E3450;',
      'border-radius:16px;padding:4px 12px;text-decoration:none;font-size:0.82rem;font-weight:600;',
      'font-family:inherit;white-space:nowrap;cursor:pointer;transition:all .15s;line-height:1.4}',
    '.page-nav .nav-link:hover{color:#E8EAF0;border-color:#7B82A0}',
    '.page-nav .nav-link.active{color:#000;background:#FFD700;border-color:#FFD700;font-weight:700}',
    '.nav-dropdown{position:relative}',
    '.nav-dropdown .nav-link .caret{font-size:.6em;margin-left:3px;vertical-align:middle}',
    '.nav-menu{display:none;position:absolute;top:calc(100% + 6px);left:0;min-width:190px;',
      'background:#21263A;border:1px solid #2E3450;border-radius:10px;padding:6px;z-index:150;',
      'box-shadow:0 16px 40px rgba(0,0,0,.45)}',
    '.nav-menu.align-right{left:auto;right:0}',
    '.nav-dropdown:hover .nav-menu,.nav-dropdown.open .nav-menu{display:block}',
    '.nav-menu a{display:block;color:#E8EAF0;text-decoration:none;font-size:.82rem;font-weight:600;',
      'padding:8px 12px;border-radius:7px;white-space:nowrap}',
    '.nav-menu a .nav-sub{display:block;color:#7B82A0;font-size:.7rem;font-weight:500;margin-top:1px}',
    '.nav-menu a:hover{background:#1A1D27;color:#FFD700}',
    '.nav-menu a.active{color:#FFD700}'
  ].join('');

  function injectCss() {
    if (document.getElementById('navCss')) return;
    var s = document.createElement('style');
    s.id = 'navCss';
    s.textContent = CSS;
    (document.head || document.documentElement).appendChild(s);
  }

  var here = (location.pathname.split('/').pop() || 'index.html');

  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
  }); }

  function render(root) {
    var html = '';
    NAV.forEach(function (item) {
      var isActive = (item.match || []).indexOf(here) !== -1;
      if (item.menu) {
        html += '<div class="nav-dropdown">';
        html += '<button type="button" class="nav-link' + (isActive ? ' active' : '') + '">' +
                esc(item.label) + '<span class="caret">▾</span></button>';
        html += '<div class="nav-menu' + (item.alignRight ? ' align-right' : '') + '">';
        item.menu.forEach(function (m) {
          var mActive = (m.href.split('/').pop() === here);
          html += '<a href="' + esc(m.href) + '"' + (mActive ? ' class="active"' : '') + '>' +
                  esc(m.label) + (m.sub ? '<span class="nav-sub">' + esc(m.sub) + '</span>' : '') + '</a>';
        });
        html += '</div></div>';
      } else {
        html += '<a class="nav-link' + (isActive ? ' active' : '') + '" href="' +
                esc(item.href) + '">' + esc(item.label) + '</a>';
      }
    });
    root.innerHTML = html;

    // Click-to-toggle for touch devices (hover handles desktop).
    root.querySelectorAll('.nav-dropdown').forEach(function (dd) {
      var btn = dd.querySelector('.nav-link');
      btn.addEventListener('click', function (e) {
        e.preventDefault();
        var wasOpen = dd.classList.contains('open');
        root.querySelectorAll('.nav-dropdown.open').forEach(function (o) { o.classList.remove('open'); });
        if (!wasOpen) dd.classList.add('open');
      });
    });
    document.addEventListener('click', function (e) {
      if (!root.contains(e.target)) {
        root.querySelectorAll('.nav-dropdown.open').forEach(function (o) { o.classList.remove('open'); });
      }
    });
  }

  function init() {
    var root = document.getElementById('mainNav');
    if (root) { injectCss(); render(root); }
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
