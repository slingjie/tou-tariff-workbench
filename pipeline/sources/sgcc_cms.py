"""Shared adapter for State Grid provincial sites on the common CMS ("/html/main/colNNNN/...").

Layout of the CMS
-----------------
* list pages   : /html/main/col<ID>/column_<ID>_<page>.html   (page 1..N, "分<b>N</b>页")
* articles     : /html/main/col<ID>/YYYY-MM/DD/<ts>_1.html     (发布日期：YYYY-MM-DD in the body)
* attachments  : /html/files/YYYY-MM/DD/<ts>.<ext>              (pdf / xls / xlsx / doc / jpg / png)

A region module builds a ``CMSRegion`` (host, column id, title regex …) and re-exports the bound
functions so it satisfies the ``pipeline.run`` contract (REGION, _months, discover, download, parse,
policy_doc_row, tou_rules, …).  Region modules only add config and table quirks.

Discovery
---------
1. Live: walk the column list pages, match titles (``title_res``; each regex must capture year, month),
   open every matching article, collect /html/files/ attachments from the article body.
2. Seed fallback: when the list HTML is unavailable (Ruishu "$_ts" JS anti-bot wall on sh.sgcc.com.cn,
   geo-block timeouts on others) the module may ship ``data/raw/<region>/seed.json`` – article and
   attachment URLs gathered once in a real browser.  Attachments under /html/files/ are static and
   download fine with curl even when the HTML is protected.
3. Versions: when a month has several articles (更正/修订/重新发布) the one with the latest publish date /
   timestamp wins, and titles containing 更正|修订|修正 win ties.  Inside one article, when several files of
   the preferred type exist, the newest <ts> wins (上海 2026-02 silently replaced its xls on 2026-02-02).

Parsing
-------
``parse_grid(grid, ...)`` maps a 2-D table (from xls / pdfplumber / OCR) to tariff_month rows by
HEADER TEXT, never by position: header cells are read bottom-up per column, first matching pattern wins.
Label columns (left of 电压等级) give tariff_type (单一制/两部制) and an optional sub-class that the region
turns into ``category`` (Jiangsu convention: capacity splits such as 100千伏安 live in category).
Vertically merged numeric cells (代理购电价格, 线损, 系统运行费, 基金) are forward-filled.
"""
import json, os, re, subprocess
from urllib.parse import urljoin

from .. import parse_common as pc

ARTICLE_RE = re.compile(r'<a([^>]*?)href="(/html/main/col(\d+)/(\d{4}-\d{2})/(\d{2})/(\d+)_1\.html)"([^>]*)>(.*?)</a>', re.S)
FILE_RE = re.compile(r'(?:href|src)="((?:https?://[^"/]+)?/html/files/(\d{4}-\d{2})/(\d{2})/(\d+)\.(pdf|xlsx?|docx?|wps|jpe?g|png))"', re.I)
DATE_RE = re.compile(r"发布(?:日期|时间)[：:]\s*(\d{4}-\d{2}-\d{2})")
RUISHU = re.compile(rb"\$_ts\s*=|_\$dC\(\)")
CORRECTION = re.compile(r"更正|修订|修正|重新发布")
FILE_PREF = ["xls", "xlsx", "pdf", "docx", "doc", "jpg", "jpeg", "png"]


class Blocked(RuntimeError):
    pass


def months_between(start, end):
    y, m = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def _txt(html):
    return re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", html or ""))


class CMSRegion:
    def __init__(self, region, host, col_id, title_res, max_pages=6, exclude_re=None,
                 file_pref=None, alt_hosts=(), timeout=40, min_file_year=2024, live_list=True):
        self.REGION = region
        self.code = region["code"]
        self.host = host.rstrip("/")
        self.hosts = [self.host] + [h.rstrip("/") for h in alt_hosts]
        self.col_id = str(col_id)
        self.title_res = [re.compile(t) for t in title_res]
        self.exclude_re = re.compile(exclude_re) if exclude_re else None
        self.max_pages = max_pages
        self.file_pref = file_pref or FILE_PREF
        self.timeout = timeout
        self.min_file_year = min_file_year  # template images (e.g. /html/files/2016-04/…) are ignored
        self.raw_dir = os.path.join(pc.DATA, "raw", self.code)
        self.seed_path = os.path.join(self.raw_dir, "seed.json")
        # live_list=False: host known to be unreachable from the pipeline network -> go straight to seed.json
        # (additive option; default keeps the original live-first behaviour)
        self.live_list = live_list

    # ------------------------------------------------------------ HTTP
    def get(self, url, timeout=None):
        r = pc.http_get(url, timeout=timeout or self.timeout)
        if RUISHU.search(r.content[:4000]):
            raise Blocked(f"Ruishu JS anti-bot page at {url}")
        return r.content.decode("utf-8", "ignore")

    def list_url(self, page, host=None):
        return f"{host or self.host}/html/main/col{self.col_id}/column_{self.col_id}_{page}.html"

    # ------------------------------------------------------------ discovery
    def match_title(self, title):
        t = _txt(title)
        if self.exclude_re and self.exclude_re.search(t):
            return None
        for rx in self.title_res:
            m = rx.search(t)
            if m:
                return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
        return None

    def list_articles(self, wanted=None, log=print):
        """Yield dicts {month,title,article_url} from the list pages (newest first)."""
        oldest = min(wanted) if wanted else None
        for host in self.hosts:
            try:
                html = self.get(self.list_url(1, host))
            except RuntimeError as e:
                log(f"  [list] {host}: {e}")
                continue
            npages = re.search(r"分\s*<b>\s*(\d+)\s*</b>\s*页", html)
            npages = min(int(npages.group(1)) if npages else 1, self.max_pages)
            for p in range(1, npages + 1):
                if p > 1:
                    try:
                        html = self.get(self.list_url(p, host))
                    except RuntimeError as e:
                        log(f"  [list] page {p}: {e}")
                        break
                page_months = []
                for m in ARTICLE_RE.finditer(html):
                    attrs = m.group(1) + m.group(7)
                    tt = re.search(r'title="([^"]+)"', attrs)
                    title = tt.group(1) if tt else _txt(m.group(8))
                    month = self.match_title(title)
                    if not month:
                        continue
                    page_months.append(month)
                    yield {"month": month, "title": _txt(title), "article_url": host + m.group(2),
                           "url_ts": m.group(6)}
                if oldest and page_months and max(page_months) < oldest:
                    return
            return
        raise Blocked(f"no list page reachable for {self.code}")

    def article_detail(self, url):
        html = self.get(url)
        date = DATE_RE.search(re.sub(r"<[^>]+>", " ", html))
        h1 = re.search(r'<td[^>]*class="[^"]*title[^"]*"[^>]*>(.*?)</td>|<h1[^>]*>(.*?)</h1>', html, re.S)
        files, seen = [], set()
        for m in FILE_RE.finditer(html):
            u = urljoin(self.host + "/", m.group(1))
            if u in seen or int(m.group(2)[:4]) < self.min_file_year:
                continue
            seen.add(u)
            # link text right after the href (if any)
            tail = html[m.end():m.end() + 400]
            lt = re.match(r'[^>]*>(.*?)</a>', tail, re.S)
            files.append({"url": u, "ext": m.group(5).lower(), "ts": m.group(4), "text": _txt(lt.group(1)) if lt else ""})
        return {"publish_date": date.group(1) if date else None, "files": files,
                "page_title": _txt((h1.group(1) or h1.group(2)) if h1 else "")}

    def pick_files(self, files):
        """Preferred type first; inside a type the newest timestamp first (silent replacements)."""
        if not files:
            return None, []
        for ext in self.file_pref:
            same = [f for f in files if f["ext"] == ext]
            if same:
                same.sort(key=lambda f: f["ts"], reverse=True)
                if ext in ("jpg", "jpeg", "png"):
                    return "img", same
                return ext, same
        return None, []

    def _load_seed(self, wanted):
        if not os.path.exists(self.seed_path):
            return []
        seed = json.load(open(self.seed_path, encoding="utf-8"))
        return [a for a in seed.get("articles", []) if a["month"] in wanted]

    def discover(self, start, end, log=print):
        wanted = set(months_between(start, end))
        cands = {}
        try:
            if not self.live_list:
                raise Blocked(f"live list disabled for {self.code} (host unreachable from pipeline network)")
            for a in self.list_articles(wanted, log):
                if a["month"] in wanted:
                    cands.setdefault(a["month"], []).append(a)
            source = "live"
        except Blocked as e:
            log(f"  [discover] live list blocked ({e}); using seed {self.seed_path}")
            for a in self._load_seed(wanted):
                cands.setdefault(a["month"], []).append(a)
            source = "seed"
        result = {}
        for month in sorted(cands):
            arts = cands[month]
            # resolve each candidate (live: open article; seed: files already listed)
            for a in arts:
                if "files" not in a:
                    try:
                        a.update(self.article_detail(a["article_url"]))
                    except RuntimeError as e:
                        log(f"  [warn] {month}: {e}")
                        a["files"] = []
                a.setdefault("url_ts", re.sub(r"\D", "", a["article_url"].rsplit("/", 1)[-1])[:17])
            arts = [a for a in arts if a.get("files")]
            if not arts:
                log(f"  {month}: matching article(s) but no attachments")
                continue
            arts.sort(key=lambda a: (a.get("publish_date") or "", bool(CORRECTION.search(a["title"])), a["url_ts"]),
                      reverse=True)
            best = arts[0]
            kind, files = self.pick_files(best["files"])
            if not kind:
                log(f"  {month}: no usable attachment type in {best['article_url']}")
                continue
            ann = {"month": month, "title": best["title"], "article_url": best["article_url"],
                   "publish_date": best.get("publish_date"), "discovered_via": source,
                   "file_kind": kind, "file_url": files[0]["url"],
                   "file_ext": files[0]["ext"] if kind != "img" else "jpg",
                   "all_files": [f["url"] for f in best["files"]],
                   "other_versions": [a["article_url"] for a in arts[1:]]}
            if kind == "img":
                # dedupe identical images later by sha; keep order
                ann["img_urls"] = [f["url"] for f in sorted(files, key=lambda f: f["ts"])]
            if len(files) > 1 and kind != "img":
                ann["superseded_files"] = [f["url"] for f in files[1:]]
            result[month] = ann
            log(f"  {month}: {ann['file_url']} ({kind}{', +%d versions' % len(arts[1:]) if arts[1:] else ''})")
        return [result[m] for m in sorted(result)]

    # ------------------------------------------------------------ download
    def _fetch_to(self, url, path, magic=None, log=print):
        if os.path.exists(path) and os.path.getsize(path) > 1000:
            log(f"  skip (exists) {os.path.relpath(path, pc.ROOT)}")
            return path
        r = pc.http_get(url, timeout=max(self.timeout, 90))
        if magic and not any(r.content.startswith(mg) for mg in magic):
            raise RuntimeError(f"unexpected file signature for {url}: {r.content[:8]!r}")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(r.content)
        log(f"  downloaded {os.path.relpath(path, pc.ROOT)} ({len(r.content)} bytes)")
        return path

    MAGIC = {"pdf": [b"%PDF"], "xls": [b"\xd0\xcf\x11\xe0"], "xlsx": [b"PK"], "docx": [b"PK"],
             "doc": [b"\xd0\xcf\x11\xe0"], "jpg": [b"\xff\xd8"], "jpeg": [b"\xff\xd8"], "png": [b"\x89PNG"]}

    def download(self, ann, log=print):
        os.makedirs(self.raw_dir, exist_ok=True)
        if ann.get("file_kind") == "img":
            paths, shas = [], []
            for i, u in enumerate(ann["img_urls"], 1):
                ext = u.rsplit(".", 1)[-1].lower()
                p = self._fetch_to(u, os.path.join(self.raw_dir, f"{ann['month']}_{i}.{ext}"), self.MAGIC.get(ext), log)
                s = pc.sha256_file(p)
                if s in shas:  # CMS galleries often repeat the same image
                    continue
                paths.append(p); shas.append(s)
            ann["file_paths"] = paths
            ann["file_path"] = paths[0]
            ann["sha256"] = shas[0]
            ann["img_sha256"] = shas
            return paths[0]
        ext = ann["file_ext"]
        p = self._fetch_to(ann["file_url"], os.path.join(self.raw_dir, f"{ann['month']}.{ext}"), self.MAGIC.get(ext), log)
        ann["file_path"] = p
        ann["sha256"] = pc.sha256_file(p)
        return p

    def policy_doc_row(self, ann):
        return {
            "id": pc.doc_id(ann["file_url"]), "region_code": self.code, "kind": "agency_price",
            "title": ann["title"], "doc_no": None, "publish_date": ann.get("publish_date"),
            "effective_from": ann.get("effective_from") or f"{ann['month']}-01", "effective_to": ann.get("effective_to"),
            "source_url": ann["file_url"], "file_path": os.path.relpath(ann["file_path"], pc.ROOT),
            "sha256": ann["sha256"], "fetched_at": pc.now_iso(),
        }


# ======================================================================
# generic table → rows
# ======================================================================
FIELD_PATTERNS = [  # tried on each header cell bottom-up; first match wins
    ("deep_valley", r"深谷"), ("sharp", r"尖峰"), ("peak", r"高峰|^峰(时)?段?$|^峰"),
    ("flat", r"平时段|平段|^平$|平时"), ("valley", r"低谷|^谷(时)?段?$|^谷"),
    ("purchase_price", r"代理购电价|购电价格|^代理购电$"), ("line_loss", r"线损"),
    ("tdp", r"输配电价|输配电"), ("system_op", r"系统运行|系统运"), ("gov_fund", r"基金|附加"),
    ("demand_price", r"需量"), ("capacity_price", r"容量"),
    ("energy_price", r"非分时|电度用电价|^电度电价|^电度用电价格|电度电价\(元"),
]
SHARED = ("purchase_price", "line_loss", "system_op", "gov_fund")
VOLT_HEAD = re.compile(r"电压等级|电压")


def _clean(s):
    if s is None:
        return ""
    if isinstance(s, float):
        return repr(s)
    return re.sub(r"\s+", "", str(s)).replace("（", "(").replace("）", ")")


def cell_num(s, nd=6):
    if isinstance(s, (int, float)) and not isinstance(s, bool):
        return round(float(s), nd)
    s = _clean(s).replace("，", "").replace(",", "")
    if s in ("", "-", "--", "—", "——", "/", "－", "－－", "\\"):
        return None
    m = re.fullmatch(r"-?\d+(\.\d+)?", s)
    return round(float(s), nd) if m else None


def find_header(grid):
    for i, row in enumerate(grid[:15]):
        for j, c in enumerate(row):
            if VOLT_HEAD.search(_clean(c)) and len(_clean(c)) <= 8:
                return i, j
    raise RuntimeError("no 电压等级 header found")


def map_columns(grid, h0, hrows):
    ncol = max(len(r) for r in grid)
    # horizontally forward-fill parent header rows (merged cells are empty in xls/pdf)
    H = []
    for r in range(h0, h0 + hrows):
        row = [(_clean(grid[r][c]) if c < len(grid[r]) else "") for c in range(ncol)]
        H.append(row)
    colmap = {}
    for c in range(ncol):
        for r in range(len(H) - 1, -1, -1):  # bottom-up: leaf header first
            txt = H[r][c]
            if not txt:
                continue
            hit = None
            for f, pat in FIELD_PATTERNS:
                if f in colmap.values():
                    continue
                if re.search(pat, txt):
                    hit = f
                    break
            if hit:
                colmap[c] = hit
                break
    return colmap


def parse_grid(grid, month, category_fn=None, voltage_fn=None, stop_re=r"^(备注|注[:：1]|说明)", log=print, nd=6):
    """grid: list of rows (list of cell values). Returns tariff_month row dicts (no doc/region).
    nd: decimals kept per numeric cell (additive option, default 6; 江西 prints 7 decimals)."""
    h0, vcol = find_header(grid)
    # header rows: until the first row whose voltage cell parses as a voltage
    hr = 1
    while h0 + hr < len(grid) and hr < 5:
        v = _clean(grid[h0 + hr][vcol] if vcol < len(grid[h0 + hr]) else "")
        if pc.voltage_rank(v):
            break
        hr += 1
    colmap = map_columns(grid, h0, hr)
    if "energy_price" not in colmap.values():
        # energy column header is often the merged cell right of 电压等级
        colmap.setdefault(vcol + 1, "energy_price")
    rows, carry, labels = [], {}, {}
    for r in grid[h0 + hr:]:
        cells = [_clean(x) for x in r]
        if not any(cells):
            continue
        if re.search(stop_re, cells[0] or "") or any(re.search(stop_re, c) for c in cells[:2] if c):
            break
        v = cells[vcol] if vcol < len(cells) else ""
        if voltage_fn:
            v = voltage_fn(v)
        if not pc.voltage_rank(v):
            continue
        for c in range(vcol):  # forward-fill label columns
            if c < len(cells) and cells[c] and not cell_num(cells[c]):
                labels[c] = cells[c]
                for c2 in range(c + 1, vcol):
                    labels.pop(c2, None)
        lab = [labels.get(c, "") for c in range(vcol)]
        joined = "|".join(lab)
        tariff = "两部制" if "两部制" in joined else ("单一制" if "单一制" in joined else None)
        row = {}
        for c, f in colmap.items():
            val = cell_num(r[c], nd) if c < len(r) else None
            if val is None and f in SHARED and not (c < len(cells) and cells[c] in ("/", "-", "--", "—")):
                val = carry.get(f)
            elif f in SHARED and val is not None:
                carry[f] = val
            row[f] = val
        cat = category_fn(lab, tariff) if category_fn else "工商业用电"
        row.update(month=month, tariff_type=tariff, category=cat, voltage=v, voltage_rank=pc.voltage_rank(v),
                   _labels=lab)
        rows.append(row)
    if not rows:
        raise RuntimeError("table header found but no data rows")
    for row in rows:
        row.pop("_labels", None)
    return rows


# ---------------------------------------------------------------- readers
def xls_sheets(path):
    """[(sheet_name, grid)] for .xls/.xlsx"""
    if path.lower().endswith(".xlsx"):
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True)
        return [(ws.title, [[c for c in row] for row in ws.iter_rows(values_only=True)]) for ws in wb.worksheets]
    import xlrd
    wb = xlrd.open_workbook(path)
    out = []
    for s in wb.sheets():
        out.append((s.name, [[s.cell_value(r, c) for c in range(s.ncols)] for r in range(s.nrows)]))
    return out


def grid_text(grid):
    return "".join(_clean(c) for row in grid for c in row)


def pdf_tables(path):
    """[(page_index, page_text, [table grids])] via pdfplumber."""
    import pdfplumber
    out = []
    with pdfplumber.open(path) as pdf:
        for i, pg in enumerate(pdf.pages):
            t = pg.extract_text() or ""
            out.append((i, t, [tb for tb in pg.extract_tables() if tb]))
    return out


EFFECTIVE_RE = re.compile(r"执行时间[：:]?\s*(\d{4})\s*年\s*(\d+)\s*月\s*(\d+)\s*日\s*[-－—–至到~]+\s*(?:(\d{4})\s*年\s*)?(\d+)\s*月\s*(\d+)\s*日")


def effective_dates(text, month):
    m = EFFECTIVE_RE.search(re.sub(r"\s+", "", text or ""))
    if m:
        y1, m1, d1, y2, m2, d2 = m.groups()
        y2 = y2 or y1
        return f"{int(y1):04d}-{int(m1):02d}-{int(d1):02d}", f"{int(y2):04d}-{int(m2):02d}-{int(d2):02d}"
    import calendar
    y, mm = map(int, month.split("-"))
    return f"{month}-01", f"{month}-{calendar.monthrange(y, mm)[1]:02d}"


# ---------------------------------------------------------------- TOU helpers
def segs_from_hours(spec, default="flat"):
    """spec: dict type -> list of (start,end) in hours (floats ok, end may wrap past 24).
    Returns sorted, merged [[HH:MM,HH:MM,type],…] covering 0-24."""
    slots = [default] * 96
    order = ["flat", "valley", "peak", "deep_valley", "sharp"]  # later types overwrite earlier
    for t in order:
        for a, b in spec.get(t, []):
            ia, ib = int(round(a * 4)), int(round(b * 4))
            rng = list(range(ia, ib)) if ib > ia else list(range(ia, 96)) + list(range(0, ib))
            for i in rng:
                slots[i % 96] = t
    out = []
    for i, t in enumerate(slots):
        if out and out[-1][2] == t:
            out[-1][1] = i + 1
        else:
            out.append([i, i + 1, t])

    def hm(q):
        return f"{q // 4:02d}:{(q % 4) * 15:02d}"
    return [[hm(a), hm(b), t] for a, b, t in out]


def tou_rows(code, doc, rules, id_base):
    out = []
    for i, r in enumerate(rules, 1):
        months, day_type, segs = r[:3]
        eff_from = r[3] if len(r) > 3 else doc["effective_from"]
        eff_to = r[4] if len(r) > 4 else None
        out.append({"id": id_base + i, "region_code": code, "effective_from": eff_from, "effective_to": eff_to,
                    "months": json.dumps(months), "day_type": day_type,
                    "segments": json.dumps(segs, ensure_ascii=False), "doc_id": doc["id"]})
    return out


def tou_doc(code, url, title, doc_no, publish_date, effective_from, effective_to=None):
    return {"id": pc.doc_id(url), "region_code": code, "kind": "tou_policy", "title": title, "doc_no": doc_no,
            "publish_date": publish_date, "effective_from": effective_from, "effective_to": effective_to,
            "source_url": url, "file_path": None, "sha256": None, "fetched_at": pc.now_iso()}


# ---------------------------------------------------------------- formula validation
def tou_formula_check(rows, expected_fn, tol=0.0006):
    """expected_fn(row) -> {field: expected value} from the province's float rules (published in the
    announcement notes).  Used both as a data check and as the OCR cross-validation."""
    fails = []
    for r in rows:
        try:
            exp = expected_fn(r) or {}
        except (TypeError, KeyError):
            continue
        for k, v in exp.items():
            if v is not None and r.get(k) is not None and abs(r[k] - v) > tol:
                fails.append({"month": r["month"], "category": r["category"], "tariff_type": r["tariff_type"],
                              "voltage": r["voltage"], "check": "tou_formula",
                              "detail": f"{k}={r[k]} expected≈{round(v, 6)}"})
    return fails


# ---------------------------------------------------------------- HTML tables (gov mirrors)
def html_tables(html):
    """All <table>s of a page as grids with rowspan/colspan expanded (merged cells repeated in their top-left
    cell only; the other covered cells are '' so parse_grid's forward-fill semantics apply)."""
    from html.parser import HTMLParser

    class P(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.tables, self.stack = [], []

        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if tag == "table":
                self.stack.append({"rows": [], "row": None, "cell": None})
            elif not self.stack:
                return
            elif tag == "tr":
                self.stack[-1]["row"] = []
            elif tag in ("td", "th"):
                t = self.stack[-1]
                if t["row"] is None:
                    t["row"] = []
                t["cell"] = {"text": "", "rs": int(re.sub(r"\D", "", a.get("rowspan", "1")) or 1),
                             "cs": int(re.sub(r"\D", "", a.get("colspan", "1")) or 1)}
            elif tag == "br" and self.stack[-1]["cell"] is not None:
                self.stack[-1]["cell"]["text"] += " "

        def handle_endtag(self, tag):
            if not self.stack:
                return
            t = self.stack[-1]
            if tag in ("td", "th") and t["cell"] is not None:
                t["row"].append(t["cell"]); t["cell"] = None
            elif tag == "tr" and t["row"] is not None:
                t["rows"].append(t["row"]); t["row"] = None
            elif tag == "table":
                self.tables.append(self.stack.pop()["rows"])

        def handle_data(self, data):
            if self.stack and self.stack[-1]["cell"] is not None:
                self.stack[-1]["cell"]["text"] += data

    p = P()
    p.feed(html)
    out = []
    for rows in p.tables:
        grid, occupied = [], {}
        for r, row in enumerate(rows):
            line, c = [], 0
            for cell in row:
                while (r, c) in occupied:
                    line.append(""); c += 1
                txt = re.sub(r"\s+", "", cell["text"].replace("\xa0", " "))
                for dr in range(cell["rs"]):
                    for dc in range(cell["cs"]):
                        if dr or dc:
                            occupied[(r + dr, c + dc)] = True
                line.append(txt)
                line.extend([""] * (cell["cs"] - 1))
                c += cell["cs"]
            while (r, c) in occupied:
                line.append(""); c += 1
            grid.append(line)
        out.append(grid)
    return out
