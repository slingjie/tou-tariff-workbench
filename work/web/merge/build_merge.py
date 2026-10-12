"""Merge 综合电价计算器 into 分时电价工作台 as a second tab.
Inputs: wb_src.html (workbench, data placeholder __DATA__), calc_src.html (calculator).
Output: out/index.html (+ assets copied from the workbench site dir) with data inlined."""
import re, json, shutil, os, sys
H = os.path.dirname(os.path.abspath(__file__))
wb = open(f'{H}/wb_src.html').read()
ca = open(f'{H}/calc_src.html').read()

def between(s, a, b, start=0):
    i = s.index(a, start) + len(a); j = s.index(b, i); return s[i:j]

# ---------- CSS ----------
calc_css_src = between(ca, '<style>', '</style>')
keep = []
for line in calc_css_src.splitlines():
    t = line.strip()
    if not t or t.startswith('.x-top') or t.startswith('[hidden]') or t.startswith('@media') or t.startswith('.x-form') or t.startswith('.x-f ') or t.startswith('.x-f>') or t.startswith('.x-f '):
        continue
    if t.startswith('.x-f') and not t.startswith('.x-flags'):
        continue
    # prefix every selector in the rule
    sel, body = t.split('{', 1)
    sel = ', '.join('#tabCalc ' + s.strip() for s in sel.split(','))
    keep.append('      ' + sel + ' {' + body)
calc_css = '\n'.join(keep) + '''
      .x-sel input { min-height: 36px; max-width: 100%; padding: 6px 10px; border-radius: var(--r-s); border: 1px solid var(--stroke-primary); background: var(--fill-primary-on); color: var(--text-primary); font: var(--type-small); }
      .x-tabs { flex-basis: 100%; display: flex; gap: 6px; margin-top: 2px; }
      .x-tab { all: unset; cursor: pointer; padding: 6px 14px; border-radius: 999px; font: var(--type-small); color: var(--text-secondary); border: 1px solid var(--stroke-secondary); }
      .x-tab:hover { background: var(--hover-wash); }
      .x-tab[aria-selected="true"] { background: var(--fill-primary); color: var(--text-primary-on); border-color: var(--fill-primary); }
      .x-tab:focus-visible { outline: 2px solid var(--fill-primary); outline-offset: 2px; }
      #tabCalc .x-cform { display: flex; flex-wrap: wrap; align-items: end; gap: 10px 14px; }
      #tabCalc .x-cform .x-sel { min-width: 120px; }
      #tabCalc .x-dtbox { display: flex; flex-wrap: wrap; align-items: end; gap: 10px 14px; }
      #tabCalc .x-chk { display: flex; align-items: center; gap: 8px; min-height: 36px; font: var(--type-caption); color: var(--text-secondary); cursor: pointer; }
      #tabCalc .x-chk input { width: 18px; height: 18px; margin: 0; accent-color: var(--fill-primary); }
      #tabCalc .x-quick { flex-basis: 100%; display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
      #tabCalc .x-cfine { margin-top: 10px; display: grid; gap: 4px; }
      #tabCalc .x-cfine:empty { display: none; }
      #tabCalc .x-cblock { margin-top: 14px; }
      #tabCalc .x-notice ul { margin-top: 6px; }
      #tabCalc .x-fold > summary { list-style: none; cursor: pointer; }
      #tabCalc .x-fold > summary::-webkit-details-marker { display: none; }
      #tabCalc .x-fold > summary h2::after { content: "▾"; margin-left: auto; color: var(--text-secondary); }
      #tabCalc .x-fold:not([open]) > summary h2 { margin-bottom: 0; }
      #tabCalc .x-fold:not([open]) > summary h2::after { content: "▸"; }
      #tabCalc .x-card > summary > h2 { font: var(--type-body-strong); margin-bottom: 12px; display: flex; flex-wrap: wrap; gap: 6px 10px; align-items: baseline; }
      #tabCalc .x-card > summary > h2 small { font: var(--type-caption); color: var(--text-secondary); }
      #tabCalc .x-card .chart { margin-top: 16px; }
      [hidden] { display: none !important; }
      @media (max-width: 599px) {
        .x-sel input { min-height: var(--tap); width: 100%; }
        #tabCalc .x-cform .x-sel { flex: 1 1 40%; }
        .x-tab { flex: 1 1 0; text-align: center; padding: 10px 8px; }
      }'''
wb = wb.replace('    </style>', calc_css + '\n    </style>\n    <link rel="stylesheet" href="hark-charts.css" />', 1)

# ---------- header tabs + wrap workbench main ----------
wb = wb.replace('''<label class="x-sel">电压等级<select id="sV"></select></label>
      </header>''', '''<label class="x-sel">电压等级<select id="sV"></select></label>
        <nav class="x-tabs" role="tablist" aria-label="视图">
          <button type="button" class="x-tab" role="tab" id="tbWb" aria-controls="tabWb" aria-selected="true">工作台</button>
          <button type="button" class="x-tab" role="tab" id="tbCalc" aria-controls="tabCalc" aria-selected="false">综合电价计算器</button>
        </nav>
      </header>''')
assert 'x-tabs' in wb
main_open = '<main class="main">\n        <p class="x-notice" id="notice"></p>\n        <div class="x-grid">'
assert main_open in wb
wb = wb.replace(main_open, '<main class="main">\n        <div id="tabWb" role="tabpanel" aria-labelledby="tbWb">\n        <p class="x-notice" id="notice"></p>\n        <div class="x-grid">')

calc_html = '''
        <div id="tabCalc" role="tabpanel" aria-labelledby="tbCalc" hidden>
        <div class="x-grid">
          <section class="x-card" id="calc"><h2>日内时间窗 <small>区域、月份、分类·计价、电压等级随顶部选择；数据 2025-11 至 2026-10，元/kWh</small></h2>
            <form class="x-cform" id="form" onsubmit="return false">
              <div hidden><select id="region" aria-hidden="true" tabindex="-1"></select><select id="combo" aria-hidden="true" tabindex="-1"></select><select id="volt" aria-hidden="true" tabindex="-1"></select></div>
              <label class="x-sel">开始时刻<input type="time" id="start" step="900" value="08:00" /></label>
              <label class="x-sel">结束时刻<input type="time" id="end" step="900" value="16:00" /></label>
              <div class="x-dtbox" id="dtBox" hidden>
                <label class="x-sel">日类型<select id="dt"></select></label>
                <label class="x-chk" id="wtBox" hidden><input type="checkbox" id="wt" /> 按实际日历加权（特殊日按天数计入）</label>
              </div>
              <div class="links x-quick">
                <span class="x-cap">快捷：</span>
                <button type="button" class="btn" data-w="08:00|16:00">08:00–16:00</button>
                <button type="button" class="btn" data-w="00:00|00:00">全天 00:00–24:00</button>
                <button type="button" class="btn" data-w="08:00|20:00">白班 08:00–20:00</button>
                <button type="button" class="btn" data-w="22:00|06:00">夜间 22:00–06:00</button>
                <span class="x-win" id="winInfo" aria-live="polite"></span>
              </div>
            </form>
            <div class="x-cfine">
              <p class="fineprint" id="dtNote" hidden></p>
              <p class="fineprint">结束早于开始时跨午夜计算（如 22:00→06:00 = 22:00–24:00 + 00:00–06:00）；开始等于结束为全天 24 小时。</p>
            </div>
            <p class="x-notice x-cblock" id="msg" hidden aria-live="polite"><strong>无法计算：</strong><span id="msgText"></span></p>
            <div class="x-kpis x-dep x-cblock" id="stats"></div>
            <div class="x-notice x-dep x-cblock" id="flagBox" hidden><strong>计算提示</strong><ul class="x-flags" id="flags"></ul></div>
            <details class="x-det x-dep x-cblock" id="notesDet">
              <summary>区域数据说明（管线原始备注）</summary>
              <ul class="x-notes" id="notes"></ul>
            </details>
          </section>
          <section class="x-card x-dep" id="monthly"><h2>12 个月逐月窗口综合电价 <small>灰底行为查看月份；末行为年度均值</small></h2>
            <div class="x-stripbox">
              <div class="x-pick">
                <label>查看月份 <select id="sm" aria-label="24 小时分时条的月份"></select></label>
                <p id="stripSub"></p>
              </div>
              <div id="strip"></div>
            </div>
            <div class="table-wrap x-cblock">
              <table>
                <thead><tr><th scope="col">月份</th><th scope="col">窗口内时段小时数</th><th scope="col">尖峰</th><th scope="col">高峰</th><th scope="col">平段</th><th scope="col">低谷</th><th scope="col">深谷</th><th scope="col">窗口综合电价</th><th scope="col">全天综合电价</th><th scope="col">日类型</th><th scope="col">状态/备注</th></tr></thead>
                <tbody id="mBody"></tbody>
              </table>
            </div>
            <figure class="chart">
              <figcaption class="chart__cap" id="chartCap">窗口综合电价与全天综合电价<small>2025-11 至 2026-10，元/kWh</small></figcaption>
              <div class="chart__canvas"><canvas id="chart" role="img" aria-label="12 个月窗口综合电价与全天综合电价折线图"></canvas></div>
            </figure>
          </section>
          <details class="x-card x-s6 x-dep x-fold" id="process" open><summary><h2>逐段核对窗口综合电价 <small id="procSub"></small></h2></summary>
            <div class="x-pick"><label>月份 <select id="pm" aria-label="计算过程的月份"></select></label></div>
            <div class="breakdown" id="proc"></div>
          </details>
          <details class="x-card x-s6 x-dep x-fold" id="volts" open><summary><h2>同一类别下各电压等级 <small id="voltSub"></small></h2></summary>
            <div class="table-wrap">
              <table>
                <thead><tr><th scope="col">电压等级</th><th scope="col">最新月窗口综合电价</th><th scope="col">年度综合电价</th></tr></thead>
                <tbody id="vBody"></tbody>
              </table>
            </div>
          </details>
          <section class="x-card" id="nation"><h2>各区域代表性电价的窗口综合电价 <small>每区域取 1-10 千伏，单一制优先；无此电压时取最低电压等级</small></h2>
            <details class="x-det" id="natDet">
              <summary>展开全国对比（按所选时间窗计算）</summary>
              <div class="table-wrap">
                <table>
                  <thead><tr>
                    <th scope="col"><button type="button" class="x-sort" data-k="name" aria-sort="none">区域</button></th>
                    <th scope="col">分类·计价</th>
                    <th scope="col">电压</th>
                    <th scope="col"><button type="button" class="x-sort" data-k="latest" aria-sort="none">最新月窗口综合电价</button></th>
                    <th scope="col"><button type="button" class="x-sort" data-k="annual" aria-sort="none">年度综合电价</button></th>
                  </tr></thead>
                  <tbody id="nBody"></tbody>
                </table>
              </div>
            </details>
          </section>
          <details class="x-card x-dep x-fold" id="export" open><summary><h2>导出月度明细与汇总 <small>CSV，UTF-8</small></h2></summary>
            <div class="links">
              <button type="button" class="btn" id="copyBtn">复制 CSV</button>
              <button type="button" class="btn" id="dlBtn">下载 CSV 文件</button>
            </div>
            <p class="fineprint" id="exStatus" aria-live="polite">下载可能被浏览器沙箱拦截；拦截时请复制下方文本。</p>
            <textarea class="x-csv" id="csv" readonly aria-label="CSV 文本"></textarea>
          </details>
        </div>
        <p class="fineprint x-cblock">窗口综合电价 = Σ(窗口内各时段时长 × 时段电价) ÷ 窗口时长；每月按当月分时规则与价格计算，年度值为近 12 个月月度值的算术平均。不涉及用电量；不含需量/容量电费；有确定特殊日表的区域（安徽、上海、浙江、江西、山西、江苏、湖北）默认按实际日历加权，节假日依国办发明电〔2025〕7号；其他区域默认按普通日规则，可在「日类型」切换尖峰日、高温日、节假日、周末规则。</p>
        </div>
      </main>'''
# close tabWb div before </main>
idx = wb.index('      </main>')
wb = wb[:idx] + '        </div>\n' + calc_html + wb[idx + len('      </main>'):]

# ---------- workbench JS hooks ----------
wbjs_old = '''    C.np = C.ok && !!C.row && !C.pr.some(function (x) { return x.p != null; });
    render();
  }'''
assert wbjs_old in wb
wb = wb.replace(wbjs_old, '''    C.np = C.ok && !!C.row && !C.pr.some(function (x) { return x.p != null; });
    render();
    if (window.__calcSync) window.__calcSync(S);
  }
  window.__wbState = function () { return S; };
  window.__wbSetMonth = function (m) { if ((R[S.r].months || []).indexOf(m) >= 0 && S.m !== m) { S.m = m; sync(); } };
  window.__wbSetRegion = function (r) { if (R[r] && S.r !== r) { S.r = r; sync(); } };
  window.__wbRender = function () { render(); };''')

# ---------- calculator JS ----------
cjs = between(ca, '    <script src="hark-charts.js"></script>\n    <script>', '    </script>\n  </body>')
def rep(old, new):
    global cjs
    assert old in cjs, old[:60]
    cjs = cjs.replace(old, new)
rep('''function stat(fig, label) { return '<div class="stat"><span class="stat__fig">' + fig + '</span><span class="stat__label">' + label + "</span></div>"; }''',
    '''function stat(fig, label) { return '<div class="x-kpi"><span>' + label + "</span><b>" + fig + "</b></div>"; }''')
rep('''    if (state) { renderTable(); renderStrip(); renderProc(); }
  }''', '''    if (state) { renderTable(); renderStrip(); renderProc(); }
    if (window.__wbSetMonth) window.__wbSetMonth(v);
  }''')
rep('''  fillCombos();
  fillDt();
  render();
})();''', '''  /* ---------- link to the workbench's shared selectors ---------- */
  var active = false;
  function applyWb(S0) {
    if (!S0 || !B.regions[S0.r]) return;
    var regChanged = regSel.value !== S0.r;
    regSel.value = S0.r;
    if (regChanged) fillCombos();
    var ix = combos.findIndex(function (c) { return S0.ct && c.cat + " · " + c.tt === S0.ct; });
    if (ix >= 0 && String(ix) !== comboSel.value) { comboSel.value = String(ix); fillVolts(); }
    if (S0.v && [].some.call(voltSel.options, function (o) { return o.value === S0.v; })) voltSel.value = S0.v;
    fillDt();
    if (S0.m && MONTHS.indexOf(S0.m) >= 0) selM = S0.m;
  }
  window.__calcSync = function (S0) { applyWb(S0); if (active) render(); };
  window.__calcActivate = function (on) { active = !!on; if (active) render(); };
  window.__calcWin = function () { return { from: sIn.value, to: eIn.value }; };
  window.__calcSetWin = function (a, b) { if (a) sIn.value = a; if (b) eIn.value = b; if (active) render(); };
  fillCombos();
  applyWb(window.__wbState && window.__wbState());
  fillDt();
})();''')

glue = r'''
(function () {
  var $ = function (id) { return document.getElementById(id); };
  var cur = "wb";
  function setHash() {
    var h = "#wb";
    if (cur === "calc") {
      var S = window.__wbState ? window.__wbState() : {}, w = window.__calcWin();
      h = "#calc?region=" + encodeURIComponent(S.r || "") + "&from=" + encodeURIComponent(w.from) + "&to=" + encodeURIComponent(w.to);
    }
    try { history.replaceState(null, "", h); } catch (e) {}
  }
  function show(t) {
    cur = t === "calc" ? "calc" : "wb";
    $("tabWb").hidden = cur !== "wb"; $("tabCalc").hidden = cur !== "calc";
    $("tbWb").setAttribute("aria-selected", String(cur === "wb")); $("tbCalc").setAttribute("aria-selected", String(cur === "calc"));
    window.__calcActivate(cur === "calc");
    if (cur === "wb" && window.__wbRender) window.__wbRender();
    setHash();
  }
  $("tbWb").onclick = function () { show("wb"); };
  $("tbCalc").onclick = function () { show("calc"); };
  document.addEventListener("change", function () { if (cur === "calc") setTimeout(setHash, 0); });
  document.addEventListener("click", function (e) { if (cur === "calc" && e.target.closest && e.target.closest("[data-w]")) setTimeout(setHash, 0); });
  if (window.matchMedia && window.matchMedia("(max-width: 599px)").matches) ["process", "volts", "export"].forEach(function (id) { $(id).open = false; });
  var h = location.hash || "";
  if (h.indexOf("#calc") === 0) {
    var q = {}; (h.split("?")[1] || "").split("&").forEach(function (p) { var kv = p.split("="); if (kv[0]) q[kv[0]] = decodeURIComponent(kv[1] || ""); });
    if (q.region) window.__wbSetRegion(q.region);
    var ok = function (v) { return /^\d{2}:\d{2}$/.test(v || "") ? v : null; };
    window.__calcSetWin(ok(q.from), ok(q.to));
    show("calc");
  }
})();
'''
scripts = ('    <script src="chart.js"></script>\n    <script src="datalabels.js"></script>\n    <script src="hark-charts.js"></script>\n'
           '    <script>' + cjs + '    </script>\n    <script>' + glue + '    </script>\n  </body>')
assert wb.count('  </body>') == 1
wb = wb.replace('  </body>', scripts.split('  </body>')[0].rstrip('\n') + '\n  </body>', 1) if False else wb
# insert after the workbench script (which ends right before </body>)
wb = wb.replace('    </script>\n  </body>', '    </script>\n' + scripts, 1)
wb = wb.replace('<title>分时电价工作台</title>', '<title>分时电价工作台</title>')
open(f'{H}/merged_template.html', 'w').write(wb)

data = open('/workspace/projects/55dbe641-d867-49ae-80e6-e219c2d53136/work/web/tariff_bundle.json').read()
json.loads(data)
out = f'{H}/out'; os.makedirs(out, exist_ok=True)
open(f'{out}/index.html', 'w').write(wb.replace('__DATA__', data.replace('</', '<\\/'), 1))
site = '/workspace/built/sites/88450453-a4ec-418e-9714-7d0836e2ed03'
for f in ['hark.js', 'hark.css', 'hark-hero.css', 'hark-charts.css', 'hark-charts.js', 'chart.js', 'datalabels.js']:
    shutil.copy(f'{site}/{f}', out)
print('ok', os.path.getsize(f'{out}/index.html'))
