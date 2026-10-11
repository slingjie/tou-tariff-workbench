"""国网江苏省电力有限公司 代理购电工商业用户电价 adapter.

discover(start, end) -> list of announcement dicts (one per month)
download(ann)        -> local PDF path (skips if present), fills sha256
parse(ann)           -> list of tariff_month row dicts
tou_rules()          -> (policy_doc dict, list of tou_rule dicts)
"""
import json, os, re
from urllib.parse import urljoin

from .. import parse_common as pc

REGION = {"code": "jiangsu", "name": "江苏", "grid": "sgcc", "province": "江苏省"}
BASE = "http://www.js.sgcc.com.cn"
# Each city 供电公司 republishes the same provincial announcement. Nantong posts the PDF;
# some others (Huai'an, Suzhou) post only JPG scans, so we try several and keep the first with a PDF.
SUBFOLDERS = ["gwntgdgs", "gwnjgdgs", "gwhagdgs", "gwszgdgs", "gwwxgdgs", "gwczgdgs", "gwycgdgs", "gwlyggdgs"]
MAX_LIST_PAGES = 6
RAW_DIR = os.path.join(pc.DATA, "raw", "jiangsu")
TITLE_RE = re.compile(r"关于\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*代理购电工商业用户电价的公告")


def _months(start, end):
    y, m = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def _list_articles(sub):
    """Yield (month, title, article_url) from a subfolder's list pages."""
    seen_pages = 0
    for p in range(1, MAX_LIST_PAGES + 1):
        url = f"{BASE}/xxgk/{sub}/djhsfbz/index.shtml" if p == 1 else f"{BASE}/xxgk/{sub}/djhsfbz/index_{p}.shtml"
        try:
            html = pc.http_get(url).content.decode("utf-8", "ignore")
        except RuntimeError:
            break
        seen_pages += 1
        found = 0
        for href, title in re.findall(r'<a[^>]+href="([^"]+\.shtml)"[^>]*>([^<]+)</a>', html):
            m = TITLE_RE.search(title)
            if m:
                found += 1
                yield f"{int(m.group(1)):04d}-{int(m.group(2)):02d}", title.strip(), urljoin(BASE, href)
        if f"index_{p+1}.shtml" not in html:
            break


def _article_detail(url):
    html = pc.http_get(url).content.decode("utf-8", "ignore")
    pdfs = re.findall(r'href="([^"]+\.pdf)"', html, re.I)
    docs = re.findall(r'href="([^"]+\.(?:docx?|xlsx?|wps))"', html, re.I)
    imgs = re.findall(r'src="(/images/[^"]+\.(?:jpe?g|png))"', html, re.I)
    date = re.search(r"发布时间[：:]\s*(\d{4}-\d{2}-\d{2})", html)
    return {
        "pdf": urljoin(BASE, pdfs[0]) if pdfs else None,
        "doc": urljoin(BASE, docs[0]) if docs else None,
        "imgs": [urljoin(BASE, i) for i in imgs],
        "publish_date": date.group(1) if date else None,
    }


def discover(start, end, log=print):
    wanted = set(_months(start, end))
    result = {}
    for sub in SUBFOLDERS:
        missing = wanted - set(result)
        if not missing:
            break
        for month, title, art_url in _list_articles(sub):
            if month not in missing or month in result:
                continue
            try:
                d = _article_detail(art_url)
            except RuntimeError as e:
                log(f"  [warn] {e}")
                continue
            if not (d["pdf"] or d["doc"]):
                log(f"  [{sub}] {month}: no PDF/doc on article (images only: {len(d['imgs'])}) – trying other subfolders")
                continue
            result[month] = {
                "month": month, "title": title, "article_url": art_url, "subfolder": sub,
                "file_url": d["pdf"] or d["doc"], "file_ext": "pdf" if d["pdf"] else d["doc"].rsplit(".", 1)[-1].lower(),
                "img_urls": d["imgs"], "publish_date": d["publish_date"],
            }
            log(f"  [{sub}] {month}: {result[month]['file_url']}")
    return [result[m] for m in sorted(result)]


def download(ann, log=print):
    os.makedirs(RAW_DIR, exist_ok=True)
    path = os.path.join(RAW_DIR, f"{ann['month']}.{ann['file_ext']}")
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        log(f"  skip (exists) {path}")
    else:
        r = pc.http_get(ann["file_url"], timeout=90)
        if ann["file_ext"] == "pdf" and not r.content.startswith(b"%PDF"):
            raise RuntimeError(f"not a PDF: {ann['file_url']}")
        with open(path, "wb") as f:
            f.write(r.content)
        log(f"  downloaded {path} ({len(r.content)} bytes)")
    ann["file_path"] = path
    ann["sha256"] = pc.sha256_file(path)
    return path


def policy_doc_row(ann):
    return {
        "id": pc.doc_id(ann["file_url"]), "region_code": REGION["code"], "kind": "agency_price",
        "title": ann["title"], "doc_no": None, "publish_date": ann.get("publish_date"),
        "effective_from": ann.get("effective_from") or f"{ann['month']}-01", "effective_to": ann.get("effective_to"),
        "source_url": ann["file_url"], "file_path": os.path.relpath(ann["file_path"], pc.ROOT),
        "sha256": ann["sha256"], "fetched_at": pc.now_iso(),
    }


# ======================================================================
# parse
# ======================================================================
FIELD_PATTERNS = [  # order matters: first match wins for a header string
    ("energy_price", r"电度用电价"), ("purchase_price", r"购电价|业购电"), ("line_loss", r"上网环|线损"),
    ("tdp", r"输配电价"), ("gov_fund", r"基金"), ("system_op", r"系统运"),
    ("sharp", r"尖峰"), ("peak", r"高峰"), ("flat", r"平时段"), ("valley", r"低谷"), ("deep_valley", r"深谷"),
    ("demand_price", r"最大需量"), ("capacity_price", r"变压器容"),
]
SHARED = ("purchase_price", "line_loss", "gov_fund", "system_op")  # vertically merged cells
ALL_FIELDS = [f for f, _ in FIELD_PATTERNS]


def _canon_voltage(v):
    """Keep the original wording, repair truncation ('1-10（20）千' -> '1-10（20）千伏')."""
    v = re.sub(r"\s+", "", v or "")
    if re.match(r"1-10[（(]20[)）]千?$", v) or re.match(r"1-10[（(]20[)）]千伏", v):
        return "1-10（20）千伏"
    if re.match(r"220千伏及以?上?$", v) or re.match(r"220千伏及?以?$", v):
        return "220千伏及以上"   # cell wrapped in the PDF (2026-02..05 两部制)
    return v


def _category(tariff_type, subclass):
    if tariff_type == "两部制":
        return "工商业用电"
    return f"工商业用电({subclass})" if subclass else "工商业用电"


def _effective(text, month):
    m = re.search(r"执行时间[：:]\s*(\d{4})\s*年\s*(\d+)\s*月\s*(\d+)\s*日\s*[-－—–]\s*(\d{4})\s*年\s*(\d+)\s*月\s*(\d+)\s*日", text or "")
    if m:
        a = [int(x) for x in m.groups()]
        return f"{a[0]:04d}-{a[1]:02d}-{a[2]:02d}", f"{a[3]:04d}-{a[4]:02d}-{a[5]:02d}"
    return f"{month}-01", None


def _find_table_page(pdf):
    for i, pg in enumerate(pdf.pages):
        t = pg.extract_text() or ""
        if "代理购电工商业用户电价表" in t.replace(" ", "") and "电压等级" in t.replace(" ", ""):
            return i, t
    return None, ""


def _parse_text_table(table, month):
    # header map from first 3 rows
    colmap = {}
    for c in range(len(table[0])):
        head = "".join(re.sub(r"\s+", "", str(table[r][c] or "")) for r in range(min(3, len(table))))
        for f, pat in FIELD_PATTERNS:
            if f not in colmap.values() and re.search(pat, head):
                colmap[c] = f
                break
    vcol = next(c for c in range(len(table[0])) if "电压等级" in re.sub(r"\s+", "", str(table[0][c] or "")))
    rows, carry, tariff, sub = [], {}, None, None
    for r in table[3:]:
        v = _canon_voltage(r[vcol])
        if not v or "计算关系" in str(r[0] or ""):
            continue
        t1 = re.sub(r"\s+", "", str(r[1] or ""))
        if t1 in ("两部制", "单一制"):
            tariff, sub = t1, None
        t2 = re.sub(r"\s+", "", str(r[2] or "")) if vcol > 2 else ""
        if t2:
            sub = t2
        row = {}
        for c, f in colmap.items():
            cell = r[c]
            if f in SHARED and cell is None:
                row[f] = carry.get(f)
            else:
                row[f] = pc.num(cell)
                if f in SHARED and row[f] is not None:
                    carry[f] = row[f]
        row.update(month=month, tariff_type=tariff, category=_category(tariff, sub), voltage=v,
                   voltage_rank=pc.voltage_rank(v))
        rows.append(row)
    return rows


# ---------- OCR fallback for scanned PDFs ----------
def _ocr_scanned(pdf_path, page_index, month, template_rows, log=print):
    """OCR 附表2 from a scanned page by detecting the grid and reading each numeric cell.
    Row labels (tariff/category/voltage) are assigned from the nearest text-parsed month whose
    electricity TDP column matches row-for-row (TDP is fixed within a regulatory period)."""
    import numpy as np, pypdfium2 as pdfium, pytesseract
    img = pdfium.PdfDocument(pdf_path)[page_index].render(scale=4).to_pil().convert("L")
    best = None
    for angle in (0, -90, 90):
        im = img.rotate(angle, expand=True) if angle else img
        a = np.array(im) < 140
        if a.shape[1] < a.shape[0] and angle == 0:
            continue  # table pages are landscape; portrait means rotated scan
        best = (im, a)
        txt = pytesseract.image_to_string(im.crop((0, 0, im.width, im.height // 4)), lang="eng", config="--psm 6")
        if re.search(r"20\d\d", txt):
            break
    im, a = best

    def groups(ix, gap=4):
        g = []
        for i in ix:
            if g and i - g[-1][-1] <= gap:
                g[-1].append(i)
            else:
                g.append([i])
        return [int(np.mean(x)) for x in g]

    hl_all = groups(np.where(a.mean(1) > 0.3)[0])
    top, bot = hl_all[0], hl_all[-1]
    vl = groups(np.where(a[top:bot].mean(0) > 0.6)[0])
    ncols = len(template_rows[0]["_cols"])  # numeric columns expected
    num_v = vl[-(ncols + 1):]
    ex0, ex1 = num_v[0], num_v[1]  # energy column
    hl = groups(np.where(a[:, ex0 + 5:ex1 - 5].mean(1) > 0.6)[0])
    hl = [y for y in hl if top <= y <= bot + 5]
    cfg = "--psm 7 -c tessedit_char_whitelist=0123456789.-"

    def ocr(x0, y0, x1, y1):
        cell = im.crop((x0 + 6, y0 + 6, x1 - 6, y1 - 6))
        s = pytesseract.image_to_string(cell, lang="eng", config=cfg).strip()
        return pc.num(s)

    # data rows = consecutive row bands whose energy cell parses as a price < 5
    bands = [(y0, y1) for y0, y1 in zip(hl, hl[1:]) if y1 - y0 > 25]
    data = [(y0, y1) for y0, y1 in bands if (lambda v: v is not None and 0.1 < v < 5)(ocr(ex0, y0, ex1, y1))]
    if len(data) != len(template_rows):
        raise RuntimeError(f"OCR found {len(data)} data rows, template has {len(template_rows)}")
    cols = template_rows[0]["_cols"]
    rows = []
    for (y0, y1), tpl in zip(data, template_rows):
        row = {k: tpl[k] for k in ("tariff_type", "category", "voltage", "voltage_rank")}
        row["month"] = month
        for f, x0, x1 in zip(cols, num_v, num_v[1:]):
            row[f] = None if f in SHARED else ocr(x0, y0, x1, y1)
        rows.append(row)
    # merged columns: OCR the whole data span
    yspan0, yspan1 = data[0][0], data[-1][1]
    for f, x0, x1 in zip(cols, num_v, num_v[1:]):
        if f in SHARED:
            cell = im.crop((x0 + 6, yspan0 + 6, x1 - 6, yspan1 - 6))
            s = pytesseract.image_to_string(cell, lang="eng", config="--psm 6 -c tessedit_char_whitelist=0123456789.-")
            vals = [pc.num(x) for x in s.split() if pc.num(x) is not None]
            for r in rows:
                r[f] = vals[0] if vals else None
    # Regulated 容(需)量 prices are fixed within a TDP period: if OCR disagrees with the template
    # month, trust the template and record the correction (verified visually for 2025-12).
    corrections = []
    for r, t in zip(rows, template_rows):
        for f in ("demand_price", "capacity_price"):
            if r.get(f) != t.get(f):
                corrections.append(f"{r['voltage']} {r['tariff_type']} {f}: OCR={r.get(f)} -> {t.get(f)}")
                r[f] = t.get(f)
    if corrections:
        log("  OCR corrections from template: " + "; ".join(corrections))
    rows[0]["_ocr_corrections"] = corrections
    # sanity: TDP must match template row-for-row
    mism = [(r["voltage"], r["tdp"], t["tdp"]) for r, t in zip(rows, template_rows) if r["tdp"] != t["tdp"]]
    if mism:
        raise RuntimeError(f"OCR TDP mismatch vs template: {mism}")
    return rows


def parse(ann, template=None, log=print):
    """Parse 附表2. `template` = rows from a text-parsed month with same layout (used for scanned PDFs)."""
    import pdfplumber
    path = ann["file_path"]
    with pdfplumber.open(path) as pdf:
        idx, text = _find_table_page(pdf)
        if idx is not None:
            ann["effective_from"], ann["effective_to"] = _effective(text, ann["month"])
            tables = [t for t in pdf.pages[idx].extract_tables() if t and len(t) > 5]
            if not tables:
                raise RuntimeError("附表2 page found but no table extracted")
            rows = _parse_text_table(tables[0], ann["month"])
            ann["parse_method"] = "pdfplumber"
            return rows
        # no text layer -> scanned
        no_text = all(not (p.extract_text() or "").strip() for p in pdf.pages)
        n = len(pdf.pages)
    if not no_text:
        raise RuntimeError("附表2 not found in text PDF")
    if not template:
        raise RuntimeError("scanned PDF and no template month available for OCR label mapping")
    import calendar
    y, m = map(int, ann["month"].split("-"))
    ann["effective_from"], ann["effective_to"] = f"{ann['month']}-01", f"{ann['month']}-{calendar.monthrange(y, m)[1]:02d}"
    last_err = None
    for pi in [n - 1] + list(range(n - 2, -1, -1)):
        try:
            rows = _ocr_scanned(path, pi, ann["month"], template, log)
            ann["parse_method"] = f"ocr(tesseract eng, page {pi+1})"
            ann["ocr_corrections"] = rows[0].pop("_ocr_corrections", [])
            return rows
        except Exception as e:  # noqa
            last_err = e
            if pi < n - 2:  # tried last two pages – stop (no loops > 2)
                break
    raise RuntimeError(f"OCR failed: {last_err}")


def make_template(rows, need_sharp):
    """Column order + labels for OCR, built from a text-parsed month."""
    cols = ["energy_price", "purchase_price", "line_loss", "tdp", "gov_fund", "system_op"]
    cols += (["sharp"] if need_sharp else []) + ["peak", "flat", "valley", "demand_price", "capacity_price"]
    out = []
    for r in rows:
        t = dict(r)
        t["_cols"] = cols
        out.append(t)
    return out


# ======================================================================
# TOU rules – 苏发改价格发〔2025〕426号 (effective 2025-06-01)
# ======================================================================
TOU_POLICY = {
    "url": "http://fzggw.jiangsu.gov.cn/art/2025/4/30/art_284_11557001.html",
    "title": "省发展改革委关于优化工商业分时电价结构促进新能源消纳降低企业用电成本支持经济社会发展的通知",
    "doc_no": "苏发改价格发〔2025〕426号", "publish_date": "2025-04-30", "effective_from": "2025-06-01",
}
SA = [["00:00", "02:00", "flat"], ["02:00", "06:00", "valley"], ["06:00", "10:00", "flat"],
      ["10:00", "14:00", "valley"], ["14:00", "15:00", "flat"], ["15:00", "22:00", "peak"], ["22:00", "24:00", "flat"]]
SW = [["00:00", "06:00", "valley"], ["06:00", "11:00", "flat"], ["11:00", "13:00", "valley"],
      ["13:00", "14:00", "flat"], ["14:00", "22:00", "peak"], ["22:00", "24:00", "flat"]]
SUMMER_SHARP = [["00:00", "06:00", "valley"], ["06:00", "11:00", "flat"], ["11:00", "13:00", "valley"],
                ["13:00", "14:00", "flat"], ["14:00", "15:00", "sharp"], ["15:00", "19:30", "peak"],
                ["19:30", "21:30", "sharp"], ["21:30", "22:00", "peak"], ["22:00", "24:00", "flat"]]
WINTER_SHARP = [["00:00", "06:00", "valley"], ["06:00", "11:00", "flat"], ["11:00", "13:00", "valley"],
                ["13:00", "14:00", "flat"], ["14:00", "18:00", "peak"], ["18:00", "20:00", "sharp"],
                ["20:00", "22:00", "peak"], ["22:00", "24:00", "flat"]]


def _with_deep_valley(segs):
    """Holiday variant: 10:00-14:00 becomes deep_valley, rest unchanged."""
    out = []
    for s, e, t in segs:
        if e <= "10:00" or s >= "14:00":
            out.append([s, e, t])
            continue
        if s < "10:00":
            out.append([s, "10:00", t])
        if e > "14:00":
            out.append(["14:00", e, t])
    out.append(["10:00", "14:00", "deep_valley"])
    return sorted(out, key=lambda x: x[0])


def tou_rules():
    doc = {
        "id": pc.doc_id(TOU_POLICY["url"]), "region_code": REGION["code"], "kind": "tou_policy",
        "title": TOU_POLICY["title"], "doc_no": TOU_POLICY["doc_no"], "publish_date": TOU_POLICY["publish_date"],
        "effective_from": TOU_POLICY["effective_from"], "effective_to": None, "source_url": TOU_POLICY["url"],
        "file_path": None, "sha256": None, "fetched_at": pc.now_iso(),
    }
    eff = TOU_POLICY["effective_from"]
    rules = [
        ([3, 4, 5, 9, 10, 11], "normal", SA),
        ([6, 2], "normal", SW),
        ([7, 8], "normal", SUMMER_SHARP),
        ([12, 1], "normal", WINTER_SHARP),
        ([5, 10], "holiday", _with_deep_valley(SA)),   # 五一 / 国庆
        ([2], "holiday", _with_deep_valley(SW)),        # 春节 (Feb in 2026)
        ([1], "holiday", _with_deep_valley(WINTER_SHARP)),  # 春节 when it falls in January
    ]
    out = []
    for i, (months, day_type, segs) in enumerate(rules, 1):
        out.append({"id": 1000 + i, "region_code": REGION["code"], "effective_from": eff, "effective_to": None,
                    "months": json.dumps(months), "day_type": day_type,
                    "segments": json.dumps(segs, ensure_ascii=False), "doc_id": doc["id"]})
    return doc, out


TOU_NOTES = [
    "尖峰 (sharp) applies only to 315千伏安及以上工业用电: the 购电价格 part of the 峰段 price is raised a further 20% "
    "(sharp = peak + 0.2 × purchase_price × (1+peak uplift)). Other users bill sharp hours at 峰 (peak); "
    "in tariff rows where sharp is NULL, treat 'sharp' segments as 'peak'.",
    "深谷 (deep_valley) applies only to 315千伏安及以上工业用电 on 春节/五一/国庆 holidays (dates per national holiday notice): "
    "the 购电价格 part of the 谷段 price is cut a further 20%, 10:00-14:00. Others bill those hours per the normal pattern. 附表2 does not list a 深谷 price.",
    "全体商业用户 and 100千伏安以下工业用户 who opt into TOU may choose the seasonal schedule or the 春秋 schedule all year.",
    "Peak uplift on 购电价格: 两部制 80%, 单一制≥100kVA 70%, 单一制<100kVA 60%; valley discount 65%.",
    "电热锅炉(蓄冰制冷, non-industrial) use a 2-period schedule (平/谷 only) – not stored as a tou_rule.",
]


# ======================================================================
# region-specific validation: TOU prices must follow 426号 表3 from 购电价格
# ======================================================================
def _uplift(row):
    if row["tariff_type"] == "两部制":
        return 0.8
    return 0.6 if "以下" in row["category"] else 0.7


def extra_validate(rows, tol=0.0006):
    """peak = energy + up*P ; valley = energy - 0.65*P ; sharp = peak + 0.2*(1+up)*P  (P = 代理购电价格)."""
    fails = []
    for r in rows:
        P, e = r.get("purchase_price"), r.get("energy_price")
        if P is None or e is None:
            continue
        up = _uplift(r)
        exp = {"flat": e, "peak": e + up * P, "valley": e - 0.65 * P}
        if r.get("sharp") is not None:
            exp["sharp"] = exp["peak"] + 0.2 * (1 + up) * P
        for k, v in exp.items():
            if r.get(k) is not None and abs(r[k] - v) > tol:
                fails.append({"month": r["month"], "category": r["category"], "tariff_type": r["tariff_type"],
                              "voltage": r["voltage"], "check": "tou_formula",
                              "detail": f"{k}={r[k]} expected≈{round(v, 4)} (426号 表3)"})
    return fails
