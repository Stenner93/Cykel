/* Klassiker Manager — fælles logik for klassiker*.html.
   Data: data/klassiker.json + data/klassiker_riders.json (bygget af
   scripts/build/build_klassiker_web_data.py). Point regnes her ud fra
   placeringerne og reglerne i datafilen, så skalaen kun står ét sted.
   Alt brugeren ændrer (blokke, plan, egne startlister) gemmes i
   localStorage pr. sæson. */
(function () {
  var KM = window.KM = {};
  var D = null, RD = null;

  KM.esc = function (s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  };
  KM.$ = function (id) { return document.getElementById(id); };

  // ── Lager (tåler privat browsing / blokeret lager) ──────────────────
  KM.get = function (k, fallback) {
    try { var v = localStorage.getItem(k); return v == null ? fallback : JSON.parse(v); }
    catch (e) { return fallback; }
  };
  KM.set = function (k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} };
  KM.del = function (k) { try { localStorage.removeItem(k); } catch (e) {} };
  KM.key = function (what) { return 'km' + (D ? D.season : '') + '_' + what; };

  // ── Navne ───────────────────────────────────────────────────────────
  KM.norm = function (s) {
    return String(s || '').normalize('NFKD').replace(/[̀-ͯ]/g, '').replace(/ł/g, 'l')
      .toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  };
  // "T. L. Andresen" -> "t andresen"; "Tadej Pogačar" -> "t pogacar"
  function nameKey(s) {
    var t = String(s || '').replace(/\./g, '. ').split(/\s+/).filter(Boolean);
    if (!t.length) return '';
    var ini = t[0].charAt(0);
    var rest = t.filter(function (x) { return !/\.$/.test(x); });
    if (!/\.$/.test(t[0])) rest = rest.slice(1);
    return KM.norm(ini) + ' ' + KM.norm(rest.join(' '));
  }
  KM.nameKey = nameKey;

  // ── Indlæsning ──────────────────────────────────────────────────────
  KM.load = function () {
    return Promise.all([
      fetch('data/klassiker.json', { cache: 'no-cache' }).then(function (r) { return r.json(); }),
      fetch('data/klassiker_riders.json', { cache: 'no-cache' }).then(function (r) { return r.json(); })
    ]).then(function (a) {
      D = KM.D = a[0]; RD = a[1];
      KM.races = D.races; KM.R = D.races.length;
      KM.riders = RD.riders;
      KM.byId = {}; KM.byName = {}; KM.byKey = {};
      RD.riders.forEach(function (r) {
        KM.byId[r.id] = r;
        KM.byName[r.name] = r;
        var k = nameKey(r.name); if (k && !KM.byKey[k]) KM.byKey[k] = r;
        r._tb = {};
        (r.tb || []).forEach(function (p) { r._tb[p[0] + ':' + p[1]] = 1; });
      });
      KM.DNF = RD.codes.DNF; KM.DNS = RD.codes.DNS;
      KM.mon = {}; D.rules.monuments.forEach(function (s) { KM.mon[s] = 1; });
      return KM;
    });
  };

  KM.resolve = function (name) {
    return KM.byName[name] || KM.byKey[nameKey(name)] || null;
  };

  // ── Resultater og point ─────────────────────────────────────────────
  KM.code = function (r, y, i) {
    var a = r && r.res && r.res[y];
    return a ? a[i] : null;
  };
  KM.started = function (r, y, i) {
    var c = KM.code(r, y, i);
    return c != null && c !== KM.DNS;
  };
  /* -> null hvis rytteren ikke startede; ellers {code, place, mon, tb, pts} */
  KM.points = function (r, y, i) {
    var c = KM.code(r, y, i);
    if (c == null || c === KM.DNS) return null;
    var ru = D.rules;
    var place = c > 0 && c <= ru.scale.length ? ru.scale[c - 1] : 0;
    var mon = KM.mon[KM.races[i].slug] && c > 0 && c <= 10 ? ru.monument_bonus[c - 1] : 0;
    var tb = r._tb[y + ':' + i] ? ru.team_bonus : 0;
    return { code: c, place: place, mon: mon, tb: tb, pts: place + mon + tb };
  };
  KM.pts = function (r, y, i) { var p = KM.points(r, y, i); return p ? p.pts : 0; };

  KM.placeHtml = function (c) {
    if (c == null) return '';
    if (c === KM.DNS) return '<span class="dns">DNS</span>';
    if (c < 0) return '<span class="dnf">DNF</span>';
    var cls = c === 1 ? 'pl1' : c <= 10 ? 'pl10' : c <= 30 ? 'pl30' : c <= 55 ? 'pl55' : c <= 75 ? 'pl75' : 'pl0';
    return '<span class="' + cls + '">' + c + '</span>';
  };
  KM.kat = function (c) {
    return c ? '<span class="kat k' + c + '">K' + c + '</span>' : '<span class="kat">–</span>';
  };

  // ── Blokke (redigerbare; gemmes lokalt) ─────────────────────────────
  KM.blocks = function () {
    var raw = KM.get(KM.key('blocks'), null) || D.blocks;
    var idx = {}; KM.races.forEach(function (r, i) { idx[r.slug] = i; });
    var starts = raw.map(function (b) { return { name: b.name, s: idx[b.first] }; })
      .filter(function (b) { return b.s != null; })
      .sort(function (a, b) { return a.s - b.s; });
    if (!starts.length || starts[0].s !== 0) starts.unshift({ name: 'Blok 1', s: 0 });
    return starts.map(function (b, k) {
      var e = k + 1 < starts.length ? starts[k + 1].s - 1 : KM.R - 1;
      return { name: b.name, start: b.s, end: e };
    });
  };
  KM.saveBlocks = function (list) {
    KM.set(KM.key('blocks'), list.map(function (b) { return { name: b.name, first: KM.races[b.start].slug }; }));
  };
  KM.resetBlocks = function () { KM.del(KM.key('blocks')); };
  KM.blockOf = function (i) {
    var B = KM.blocks();
    for (var k = 0; k < B.length; k++) if (i >= B[k].start && i <= B[k].end) return k;
    return 0;
  };

  // ── Forventede startlister: datafilen + din egen uploadede version ──
  KM.localStart = function () { return KM.get(KM.key('startlists'), null); };
  KM.flag = function (rid, i) {
    var loc = KM.localStart();
    if (loc && loc.flags[rid] && loc.flags[rid][i] != null) return loc.flags[rid][i];
    var sl = D.startlists;
    return sl && sl.flags[rid] ? sl.flags[rid][i] : null;
  };
  KM.expectedCount = function (rid) {
    var n = 0, k = 0;
    for (var i = 0; i < KM.R; i++) { var f = KM.flag(rid, i); if (f === 1) n++; if (f != null) k++; }
    return { yes: n, known: k };
  };

  // Genkendelse af løbskolonner — samme regel som import_klassiker_sheets.py:
  // et løb kendes på de ord i dets slug, som intet andet løb har.
  var GENERIC = { race: 1, classic: 1, gold: 1, het: 1, de: 1, la: 1, van: 1, door: 1, grand: 1, prix: 1, gp: 1 };
  KM.raceMatcher = function () {
    var toks = KM.races.map(function (r) {
      var t = r.slug.split('-'); t.push(KM.norm(r.short)); return t;
    });
    var uniq = toks.map(function (t, i) {
      return t.filter(function (w) {
        if (w.length < 2 || GENERIC[w]) return false;
        return !toks.some(function (o, j) { return j !== i && o.indexOf(w) !== -1; });
      });
    });
    return function (header) {
      var words = KM.norm(header).split(' ');
      var hits = [];
      uniq.forEach(function (u, i) {
        var ok = u.some(function (w) {
          return words.indexOf(w) !== -1 || (w.length >= 6 && words.some(function (x) { return x.indexOf(w.slice(0, 6)) === 0; }));
        });
        if (ok) hits.push(i);
      });
      return hits.length === 1 ? hits[0] : null;
    };
  };

  var YES = ['✅', '1', 'x', 'ja', 'yes', 'true', '✓', '✔'], NO = ['❌', '0', 'nej', 'no', 'false', '✗', '✘', '-'];
  /* rows: array af arrays (fra xlsx, csv eller indsat tekst).
     -> {found: antal løb, rows: [{name, cat, flags:[1/0/null × R]}]} */
  KM.parseStartTable = function (rows) {
    var match = KM.raceMatcher(), hi = -1, cols = {};
    for (var i = 0; i < Math.min(rows.length, 10) && hi < 0; i++) {
      var c = {}, n = 0;
      (rows[i] || []).forEach(function (h, j) { var m = h ? match(h) : null; if (m != null) { c[j] = m; n++; } });
      if (n >= 3) { hi = i; cols = c; }
    }
    if (hi < 0) throw new Error('Fandt ingen overskriftsrække med løbsnavne (fx "omloop het nieuwsblad").');
    var body = rows.slice(hi + 1).filter(function (r) { return r && r.some(function (x) { return String(x || '').trim(); }); });
    var width = Math.max.apply(null, body.map(function (r) { return r.length; }));
    var isName = function (v) { return /^\s*[A-ZÀ-Þ][a-zà-ÿ]?\./.test(String(v || '')); };
    var nameCol = 0, best = -1;
    for (var j = 0; j < width; j++) {
      var s = body.filter(function (r) { return isName(r[j]); }).length;
      if (s > best) { best = s; nameCol = j; }
    }
    var catCol = null;
    for (j = 0; j < width && catCol == null; j++) {
      var k = body.filter(function (r) { return /^(kat\.?\s*)?[1-4]$/i.test(String(r[j] || '').trim()); }).length;
      if (k > body.length / 3) catCol = j;
    }
    var mark = function (v) {
      v = String(v == null ? '' : v).trim().toLowerCase();
      return YES.indexOf(v) !== -1 ? 1 : NO.indexOf(v) !== -1 ? 0 : null;
    };
    var marks = function (sh) {
      var n = 0;
      Object.keys(cols).forEach(function (jj) { body.forEach(function (r) { if (mark(r[+jj + sh]) != null) n++; }); });
      return n;
    };
    var shift = marks(1) > marks(0) * 1.2 ? 1 : 0;   // fantasytool-arkets forskydning
    var out = [];
    body.forEach(function (r) {
      var name = String(r[nameCol] || '').trim();
      if (!name || /^(unknown|player)$/i.test(name)) return;
      var flags = new Array(KM.R).fill(null);
      Object.keys(cols).forEach(function (jj) { flags[cols[jj]] = mark(r[+jj + shift]); });
      var cat = catCol != null ? +(String(r[catCol] || '').replace(/\D/g, '')) || null : null;
      out.push({ name: name, cat: cat, flags: flags });
    });
    return { found: Object.keys(cols).length, rows: out, shifted: !!shift };
  };

  /* Flet en læst tabel ind i din lokale version. Tomme celler overskriver
     aldrig et tidligere flueben — fantasytool sletter kørte løb. */
  KM.mergeLocalStart = function (parsed) {
    var loc = KM.localStart() || { flags: {}, extra: {} };
    loc.extra = loc.extra || {};
    var nNew = 0, nChg = 0, unknown = [];
    parsed.rows.forEach(function (row) {
      var r = KM.resolve(row.name);
      var id = r ? r.id : 'n:' + row.name;
      if (!r) { unknown.push(row.name); loc.extra[id] = { name: row.name, cat: row.cat }; }
      var cur = loc.flags[id] || new Array(KM.R).fill(null);
      row.flags.forEach(function (f, i) {
        if (f == null) return;
        var before = cur[i] != null ? cur[i] : KM.flag(id, i);
        if (before == null) nNew++; else if (before !== f) nChg++;
        cur[i] = f;
      });
      loc.flags[id] = cur;
    });
    loc.updated = new Date().toISOString().slice(0, 16).replace('T', ' ');
    KM.set(KM.key('startlists'), loc);
    return { nNew: nNew, nChg: nChg, unknown: unknown };
  };

  /* Læs en fil (xlsx/csv/txt) eller indsat tekst til rækker. xlsx kræver
     SheetJS, som hentes først når der faktisk vælges en xlsx-fil. */
  KM.readRows = function (fileOrText) {
    if (typeof fileOrText === 'string') return Promise.resolve(textRows(fileOrText));
    var f = fileOrText;
    if (/\.xlsx?$|\.xlsm$/i.test(f.name)) {
      return loadSheetJS().then(function () { return f.arrayBuffer(); }).then(function (buf) {
        var wb = window.XLSX.read(buf, { type: 'array' });
        var pick = wb.SheetNames.find(function (n) { return /kalender|startlist/i.test(n); }) || wb.SheetNames[0];
        return { sheet: pick, rows: window.XLSX.utils.sheet_to_json(wb.Sheets[pick], { header: 1, raw: false, defval: '' }) };
      });
    }
    return f.text().then(textRows);
  };
  function textRows(t) {
    var lines = t.replace(/\r/g, '').split('\n');
    var sep = lines[0].indexOf('\t') !== -1 ? '\t' : (lines[0].split(';').length > lines[0].split(',').length ? ';' : ',');
    return { sheet: null, rows: lines.map(function (l) { return l.split(sep).map(function (x) { return x.replace(/^"|"$/g, ''); }); }) };
  }
  function loadSheetJS() {
    if (window.XLSX) return Promise.resolve();
    return new Promise(function (ok, fail) {
      var s = document.createElement('script');
      s.src = 'https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js';
      s.onload = ok; s.onerror = function () { fail(new Error('Kunne ikke hente xlsx-læseren — gem som CSV eller indsæt teksten i stedet.')); };
      document.head.appendChild(s);
    });
  }

  // ── Faner (som på Vuelta-siden: #fane i adressen) ──────────────────
  KM.tabs = function (onShow) {
    var tabs = Array.prototype.slice.call(document.querySelectorAll('button.tab'));
    function sel(t, push) {
      tabs.forEach(function (x) {
        var on = x === t;
        x.setAttribute('aria-selected', on ? 'true' : 'false');
        var p = document.getElementById(x.getAttribute('aria-controls')); if (p) p.hidden = !on;
      });
      if (push) history.replaceState(null, '', '#' + t.id.replace(/^t-/, ''));
      if (onShow) onShow(t.id.replace(/^t-/, ''));
    }
    tabs.forEach(function (t) { t.addEventListener('click', function () { sel(t, true); }); });
    var h = (location.hash || '').slice(1);
    sel((h && document.getElementById('t-' + h)) || tabs[0], false);
  };

  /* Klistrede overskriftsrækker: hver række lægges lige under den forrige,
     uanset hvor mange rækker (gruppe, navne, filtre) tabellen har. */
  KM.stickHead = function (table) {
    var top = 0;
    Array.prototype.forEach.call(table.querySelectorAll('thead tr'), function (tr) {
      Array.prototype.forEach.call(tr.children, function (th) { th.style.top = top + 'px'; });
      top += tr.getBoundingClientRect().height;
    });
  };

  // ── Generisk filter-udtryk: ">10", ">=3", "<5", "2-6", "=0", tekst ──
  KM.filterFn = function (expr) {
    expr = String(expr || '').trim();
    if (!expr) return null;
    var m;
    if ((m = expr.match(/^(>=|<=|>|<|=)\s*(-?\d+(?:[.,]\d+)?)$/))) {
      var v = parseFloat(m[2].replace(',', '.')), op = m[1];
      return function (x) {
        if (x == null || x === '' || isNaN(x)) return false;
        return op === '>' ? x > v : op === '<' ? x < v : op === '>=' ? x >= v : op === '<=' ? x <= v : x === v;
      };
    }
    if ((m = expr.match(/^(-?\d+(?:[.,]\d+)?)\s*-\s*(-?\d+(?:[.,]\d+)?)$/))) {
      var a = parseFloat(m[1].replace(',', '.')), b = parseFloat(m[2].replace(',', '.'));
      return function (x) { return x != null && !isNaN(x) && x >= a && x <= b; };
    }
    if (/^-?\d+(?:[.,]\d+)?$/.test(expr)) {
      var n = parseFloat(expr.replace(',', '.'));
      return function (x) { return x === n; };
    }
    var t = KM.norm(expr);
    return function (x) { return KM.norm(x).indexOf(t) !== -1; };
  };
})();
