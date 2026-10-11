"""国网山西省电力有限公司 代理购电工商业用户电价 — own-CMS discovery + ocr_table.

Quirks
* Site uses its own CMS (not /html/main/col…): list pages http://www.sx.sgcc.com.cn/wbw/channels/c40145_<N>.html
  (信息公开 › 收费标准, mixed with 现货市场运营费用 notices and 输配电价表), articles
  /homepage/wbw/articles/YYYYMM/a<ID>.html ("发布时间： YYYY-MM-DD"), attachment /homepage/wbw/docs/YYYY/MM/DD/<hex>.pdf.
  Reachable with plain curl from the sandbox (2026-10-11).
* Titles: "国网山西省电力有限公司关于YYYY年M月代理购电价格公告"; 2026-03 and 2026-04 were re-issued on 2026-04-03 as
  "…代理购电价格更新" (file "…价格表-调整后(3).pdf"); the 更新 article wins (latest publish date / correction marker).
* Attachment = one-page PDF holding only an embedded 4109×5760 poster image (no text layer, light watermark) with three
  tables: 代理购电工商业用户电价表 (loaded), 执行1.5倍代理购电价格 table (skipped), 代理购电价格信息表 (read only to
  cross-check 代理购电价格 / 线损 / 系统运行费).  Downloads are large (5-30 MB) and slow: written to *.part, then
  validated with pypdfium2 before being moved into place (a truncated 2026-04 file was seen once).
* OCR cross-validation with the notice's own arithmetic: 平段 = 电量电价 = Σ components; 高峰/低谷 = 平段 ± 60%/55%·base,
  base = 当月平均购电价格 (one constant per month; 历史偏差电费折价 does not float); 尖峰 = 高峰 + 20%·1.6·base;
  深谷 = 低谷 − 20%·0.45·base.  These relations repair OCR cells only; stored 尖峰/深谷 are the printed values ('—' → NULL).
* Layout: to 2026-04 12 numeric columns (no 深谷); from 2026-05 (晋发改商品发〔2026〕15号) a 深谷 column is added (13).
  Rows: 3rd TDP period (to 2026-07) 单一制 不满1 / 1-10（20） / 35 + 两部制 1-10（20） / 35 / 110 / 220及以上 = 7;
  4th period (2026-08 →, 发改价格〔2026〕1077号) 单一制 adds 110 and 220及以上 → 9 rows.
"""
import json, os, re, subprocess

from . import sgcc_cms as cms
from . import ocr_table as ocr
from .. import parse_common as pc

REGION = {"code": "shanxi", "name": "山西", "grid": "sgcc", "province": "山西省"}
HOST = "http://www.sx.sgcc.com.cn"
LIST_URL = HOST + "/wbw/channels/c40145_{}.html"
TITLE_RE = re.compile(r"国网山西省电力有限公司关于(\d{4})年(\d{1,2})月代理购电价格")
MAX_PAGES = 6
RAW = os.path.join(pc.DATA, "raw", REGION["code"])
TOU_ID_BASE = 41000
OCR_VERSION = 3

_months = cms.months_between

V = {"lt1": "不满1千伏", "10": "1-10（20）千伏", "35": "35千伏", "110": "110千伏", "220": "220千伏及以上"}
LAYOUTS = {7: [("单一制", V[k]) for k in ("lt1", "10", "35")] + [("两部制", V[k]) for k in ("10", "35", "110", "220")],
           9: [("单一制", V[k]) for k in ("lt1", "10", "35", "110", "220")] + [("两部制", V[k]) for k in ("10", "35", "110", "220")]}
BASE_COLS = ["energy_price", "purchase_price", "line_loss", "tdp", "gov_fund", "system_op", "sharp", "peak", "flat", "valley"]
COLUMNS = {12: BASE_COLS + ["demand_price", "capacity_price"],
           13: BASE_COLS + ["deep_valley", "demand_price", "capacity_price"]}
INFO_ROWS = {"purchase_price": 4, "line_loss": 7, "system_op": 8}   # 序号 in 代理购电价格信息表
COEF = {"peak": 0.60, "valley": 0.55, "sharp_on_peak": 0.20, "deep_on_valley": 0.20}


# ---------------------------------------------------------------- discovery
def _get(url, timeout=60):
    return pc.http_get(url, timeout=timeout).content.decode("utf-8", "ignore")


def _article(url):
    h = _get(url)
    t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h))
    d = re.search(r"发布时间：\s*(\d{4}-\d{2}-\d{2})", t)
    files = re.findall(r"""['"](/homepage/wbw/docs/[^'"]+\.pdf)['"]""", h)
    att = re.findall(r"附件\d*\.?\s*:\s*(.+?\.pdf)", t)
    return d.group(1) if d else None, [HOST + f for f in files], att


def discover(start, end, log=print):
    wanted = set(_months(start, end))
    cands = {}
    for p in range(1, MAX_PAGES + 1):
        try:
            h = _get(LIST_URL.format(p))
        except RuntimeError as e:
            log(f"  [list] page {p}: {e}")
            break
        page_months = []
        for m in re.finditer(r"href='(/homepage/wbw/articles/\d{6}/a\d+\.html)'\s*>([^<]+)</a>.*?/(\d{4}-\d{2}-\d{2})/", h, re.S):
            tm = TITLE_RE.search(m.group(2))
            if not tm:
                continue
            month = f"{int(tm.group(1)):04d}-{int(tm.group(2)):02d}"
            page_months.append(month)
            if month in wanted:
                cands.setdefault(month, []).append({"title": m.group(2).strip(), "article_url": HOST + m.group(1),
                                                    "list_date": m.group(3)})
        if page_months and max(page_months) < min(wanted):
            break
    out = []
    for month in sorted(cands):
        arts = []
        for a in cands[month]:
            try:
                a["publish_date"], a["files"], a["file_names"] = _article(a["article_url"])
            except RuntimeError as e:
                log(f"  [warn] {month}: {e}")
                continue
            if a["files"]:
                arts.append(a)
        if not arts:
            log(f"  {month}: no article with a PDF attachment")
            continue
        arts.sort(key=lambda a: (a["publish_date"] or a["list_date"], "更新" in a["title"], a["article_url"]), reverse=True)
        b = arts[0]
        out.append({"month": month, "title": b["title"], "article_url": b["article_url"],
                    "publish_date": b["publish_date"] or b["list_date"], "discovered_via": "live",
                    "file_kind": "pdf", "file_url": b["files"][0], "file_ext": "pdf",
                    "file_name": (b["file_names"] or [None])[0],
                    "other_versions": [a["article_url"] for a in arts[1:]]})
        log(f"  {month}: {b['files'][0]}{' (+%d older versions)' % (len(arts) - 1) if len(arts) > 1 else ''}")
    return out


def _pdf_ok(path):
    try:
        import pypdfium2 as pdfium
        return len(pdfium.PdfDocument(path)) >= 1
    except Exception:
        return False


def download(ann, log=print):
    os.makedirs(RAW, exist_ok=True)
    path = os.path.join(RAW, f"{ann['month']}.pdf")
    if not (os.path.exists(path) and _pdf_ok(path)):
        part = path + ".part"
        last = None
        for i in range(3):      # first try + max 2 retries
            p = subprocess.run(["curl", "-sS", "-L", "-m", "300", "-A", pc.UA["User-Agent"], "-o", part,
                                "-w", "%{http_code}", ann["file_url"]], capture_output=True)
            if p.returncode == 0 and p.stdout.strip() == b"200" and _pdf_ok(part):
                os.replace(part, path)
                log(f"  downloaded {os.path.relpath(path, pc.ROOT)} ({os.path.getsize(path)} bytes)")
                break
            last = f"rc={p.returncode} http={p.stdout.decode(errors='ignore')} {p.stderr.decode(errors='ignore')[:150]}"
        else:
            raise RuntimeError(f"download failed / invalid PDF: {ann['file_url']}: {last}")
    else:
        log(f"  skip (exists) {os.path.relpath(path, pc.ROOT)}")
    ann["file_path"] = path
    ann["sha256"] = pc.sha256_file(path)
    return path


def policy_doc_row(ann):
    return {"id": pc.doc_id(ann["file_url"]), "region_code": REGION["code"], "kind": "agency_price",
            "title": ann["title"], "doc_no": None, "publish_date": ann.get("publish_date"),
            "effective_from": ann.get("effective_from") or f"{ann['month']}-01", "effective_to": ann.get("effective_to"),
            "source_url": ann["file_url"], "file_path": os.path.relpath(ann["file_path"], pc.ROOT),
            "sha256": ann["sha256"], "fetched_at": pc.now_iso()}


# ---------------------------------------------------------------- parse (OCR)
def parse(ann, template=None, log=print):
    rows = ocr.cached_parse(ann, REGION["code"], lambda: _parse(ann, log), OCR_VERSION)
    for r in rows:   # 单一制 has no 容(需)量电价 ('—' on every poster; OCR sometimes reads the dash as a digit)
        if r["tariff_type"] == "单一制":
            r["demand_price"] = r["capacity_price"] = None
    return rows


def _parse(ann, log=print):
    img = ocr.load_image(ann["file_path"])
    tables, a = ocr.find_tables(img, thr=150)
    t = None
    for tb in tables:
        if len(tb["hlines"]) < 8:
            continue
        tb["title_ocr"] = ocr.title_above(img, tb, height=int((tb["hlines"][1] - tb["hlines"][0]) * 3.2))
        if not any(x in tb["title_ocr"] for x in ("1.5", "倍", "信息")):
            t = tb
            break
    if t is None:
        raise RuntimeError(f"tariff table not found: {[x.get('title_ocr', '')[:30] for x in tables]}")
    t["hlines"] = _drop_split_rules(t["hlines"])
    tab = ocr.read_table(img, t, a, scale=1.0, log=log)
    pre = _preclean(tab)
    ncol, nrow = len(tab["data"][0][0]), len(tab["data"])
    if ncol not in COLUMNS or nrow not in LAYOUTS:
        raise RuntimeError(f"unexpected table shape {nrow} rows × {ncol} numeric columns")
    info = {}
    try:
        iv = ocr.read_info_table(img, [x for x in tables if x is not t], a, scale=1.0, want=("信息表", "价格信息"), log=log)
        info = {f: iv[k] for f, k in INFO_ROWS.items() if k in iv}
    except RuntimeError:
        pass
    rows, fixes = ocr.solve_rows(tab, COLUMNS[ncol], LAYOUTS[nrow], ann["month"], COEF, base_kind="month", tol=2e-6,
                                 log=log, info=info)
    fixes = pre + fixes + _snap_to_print(rows)
    for r in rows:
        r["category"] = "工商业用电"
    bad = [f for f in pc.validate_rows(rows) if f["check"] in ("components_sum", "components_missing", "tou_order")]
    if len({(f["tariff_type"], f["voltage"]) for f in bad}) > len(rows) // 4:
        raise RuntimeError(f"OCR unreliable: {len(bad)} failures, e.g. {bad[0]['detail']}")
    ann["effective_from"], ann["effective_to"] = cms.effective_dates("", ann["month"])
    ann["parse_method"] = "ocr(tesseract eng, embedded poster image)"
    ann["ocr_corrections"] = fixes
    ann["ocr_voltage_digits"] = tab["voltage_digits"]
    return rows


def _drop_split_rules(ys):
    """Watermark strokes / merged-cell text sometimes add rules inside one data row (2026-08: 220千伏 row split in 3,
    2026-10: a stray rule under 220千伏).  For every gap shorter than 0.75× the median row height, delete whichever of
    its two rules leaves the merged gap closest to the median."""
    ys = list(ys)
    gaps = sorted(b - a for a, b in zip(ys, ys[1:]))
    med = gaps[len(gaps) // 2]
    short = lambda a, b: b - a < 0.75 * med
    k = 0
    while k < len(ys) - 1:
        if not short(ys[k], ys[k + 1]):
            k += 1
            continue
        j = k + 1                      # maximal run of short gaps ys[k] .. ys[j]
        while j + 1 < len(ys) and short(ys[j], ys[j + 1]):
            j += 1
        if ys[j] - ys[k] >= 0.75 * med:
            del ys[k + 1:j]            # e.g. 1689 | 1708 1727 | 1746: keep the run's end rules
        else:                          # the whole run is one stray stroke next to a real rule
            del ys[k + 1:j]
            opts = []
            if k > 0:
                opts.append((abs(ys[k + 1] - ys[k - 1] - med), k))
            if k + 2 < len(ys):
                opts.append((abs(ys[k + 2] - ys[k] - med), k + 1))
            if opts:
                del ys[min(opts)[1]]
        k = max(k - 1, 0)
    return ys


def _preclean(tab):
    """Deterministic fixes of OCR strings before the arithmetic vote: a dropped decimal point in a price
    ('062140375' → 0.62140375) and in 需量/容量 ('225' → 22.5, '1336' → 33.6: a stray rule read as '1')."""
    fixes, ncol = [], len(tab["data"][0][0])
    cols = COLUMNS.get(ncol, [])
    for i, (nums, raws) in enumerate(tab["data"]):
        for j, raw in enumerate(raws):
            f = cols[j] if j < len(cols) else None
            if not raw or "." in raw or f is None:
                continue
            if f in ("demand_price", "capacity_price"):
                if re.fullmatch(r"\d{3,4}", raw):
                    new = raw[-3:-1] + "." + raw[-1]
                else:
                    continue
            elif re.fullmatch(r"0\d{6}|0\d{8}", raw):   # 6- or 8-decimal prices
                new = "0." + raw[1:]
            elif len(raw) < 6:
                # a '—' (no price printed) read as '1' / '4' / '-': blank, never derived
                fixes.append(f"row {i + 1} {f}: OCR '{raw}' -> blank ('—' cell)")
                nums[j] = None; raws[j] = ""
                continue
            else:
                continue
            fixes.append(f"row {i + 1} {f}: OCR '{raw}' -> {new} (decimal point dropped)")
            nums[j] = float(new); raws[j] = new
    return fixes


def _snap_to_print(rows):
    """The poster prints 峰/谷/尖/深 = 平段 ± D with D = round(k·base, 6) identical on every row of a month.  Cells
    re-derived by solve_rows (≈1e-7 off) are snapped to 平段 + the month's modal D."""
    from collections import Counter
    fixes = []
    for f in ("peak", "valley", "sharp", "deep_valley"):
        ds = Counter(round(r[f] - r["flat"], 8) for r in rows if r.get(f) is not None and r.get("flat") is not None)
        if not ds:
            continue
        D, n = ds.most_common(1)[0]
        if n < 2:
            continue
        for r in rows:
            if r.get(f) is None or r.get("flat") is None:
                continue
            exp = round(r["flat"] + D, 8)
            if r[f] != exp and abs(r[f] - exp) <= 3e-6:
                fixes.append(f"{r['tariff_type']} {r['voltage']} {f}: {r[f]} -> {exp} (snapped to 平段 + printed D {D})")
                r[f] = exp
    return fixes


def _period(m, rows):
    return "P4" if m >= "2026-08" else "P3"


def postprocess(parsed, anns):
    return ocr.regulated_consensus(parsed, anns, fields=("tdp", "demand_price", "capacity_price"), period_key=_period)


def extra_validate(rows):
    """Float rule: (峰−平)/0.6 == (平−谷)/0.55 == 当月 base, constant over a month's rows."""
    fails, by = [], {}
    for r in rows:
        if r.get("peak") is not None and r.get("flat") is not None:
            by.setdefault(r["month"], []).append(r)
    for m, rs in by.items():
        bs = sorted(round((r["peak"] - r["flat"]) / 0.6, 6) for r in rs)
        B = bs[len(bs) // 2]
        def exp(r):
            e = {"peak": r["flat"] + 0.6 * B, "valley": r["flat"] - 0.55 * B}
            if r.get("sharp") is not None:
                e["sharp"] = r["flat"] + 0.6 * B + 0.2 * 1.6 * B
            if r.get("deep_valley") is not None:
                e["deep_valley"] = r["flat"] - 0.55 * B - 0.2 * 0.45 * B
            return e
        fails += cms.tou_formula_check(rs, exp, tol=2e-6)
    return fails


# ---------------------------------------------------------------- TOU
# Old rule (to 2026-04-30): 晋发改商品发〔2021〕479号 (eff 2022-01-01) as restated in note 2 of every monthly notice
# 2025-11..2026-04 together with 晋发改商品发〔2023〕166号. The 479号 text is not on a reachable official page; the
# notice restatement (official 国网山西 PDF) is used as source.  New rule: 晋发改商品发〔2026〕15号 (2026-01-31,
# published 2026-02-28, effective 2026-05-01), official text on fgw.shanxi.gov.cn.
TOU_479 = ("http://www.sx.sgcc.com.cn/homepage/wbw/docs/2025/10/28/7ae79c1ac3b24ddb9695f393bf47a859.pdf",
           "山西省发展和改革委员会关于完善分时电价机制有关事项的通知（时段据国网山西代理购电价格公告注2转述）",
           "晋发改商品发〔2021〕479号", "2021-12-14", "2022-01-01", "2026-04-30")
TOU_15 = ("https://fgw.shanxi.gov.cn/sxfgwzwgk/sxsfgwxxgk/xxgkml/tz/202602/t20260228_10069158.shtml",
          "山西省发展和改革委员会关于进一步完善工商业用户分时电价政策的通知", "晋发改商品发〔2026〕15号",
          "2026-01-31", "2026-05-01", None)

OLD = {"peak": [(8, 11), (17, 23)], "flat": [(7, 8), (13, 17), (23, 24)], "valley": [(0, 7), (11, 13)]}
NEW = {
    "spring": ([3, 4, 5], {"peak": [(6, 8), (17, 24)], "valley": [(2, 5), (9, 15)],
                           "flat": [(0, 2), (5, 6), (8, 9), (15, 17)]}),
    "summer": ([6, 7, 8], {"peak": [(6, 8), (18, 24)], "valley": [(2, 5), (10, 15)],
                           "flat": [(0, 2), (5, 6), (8, 10), (15, 18)], "sharp": [(18, 21)]}),
    "autumn": ([9, 10, 11], {"peak": [(6, 8), (17, 24)], "valley": [(2, 5), (11, 15)],
                             "flat": [(0, 2), (5, 6), (8, 11), (15, 17)]}),
    "winter": ([12, 1, 2], {"peak": [(6, 8), (16, 23)], "valley": [(2, 5), (10, 15)],
                            "flat": [(23, 2), (5, 6), (8, 10), (15, 16)], "sharp": [(17, 20)]}),
}
HOLIDAY_MONTHS = {"winter": [2], "spring": [5], "autumn": [10]}   # 春节 / 劳动节 / 国庆节


def tou_rules():
    code = REGION["code"]
    d_old = cms.tou_doc(code, *TOU_479)
    d_new = cms.tou_doc(code, *TOU_15)
    old_sharp = dict(OLD, sharp=[(18, 20)])
    old = [([2, 3, 4, 5, 6, 9, 10, 11], "normal", cms.segs_from_hours(OLD), "2022-01-01", "2026-04-30"),
           ([1, 7, 8, 12], "normal", cms.segs_from_hours(old_sharp), "2022-01-01", "2026-04-30")]
    new = []
    for season, (months, spec) in NEW.items():
        new.append((months, "normal", cms.segs_from_hours(spec), "2026-05-01", None))
    for season, hm in HOLIDAY_MONTHS.items():
        spec = dict(NEW[season][1]); spec["deep_valley"] = [(13, 15)]
        new.append((hm, "holiday", cms.segs_from_hours(spec), "2026-05-01", None))
    return [(d_old, cms.tou_rows(code, d_old, old, TOU_ID_BASE)),
            (d_new, cms.tou_rows(code, d_new, new, TOU_ID_BASE + 50))]


TOU_NOTES = [
    "TOU to 2026-04-30: 晋发改商品发〔2021〕479号 (+〔2023〕166号), as restated in note 2 of each monthly notice: 高峰 8-11、17-23, "
    "平 7-8、13-17、23-24, 低谷 0-7、11-13 all year; 尖峰 18-20 in 1、7、8、12月 for 两部制 (原大工业) users only – the 1/7/8/12月 rule "
    "shows the 尖峰 window; 单一制 users treat 18-20 as 高峰 in those months. 高峰 +60%, 低谷 −55%, 尖峰 = 高峰 +20%.",
    "TOU from 2026-05-01: 晋发改商品发〔2026〕15号 (dated 2026-01-31, published 2026-02-28). Scope: all 代理购电 工商业 users "
    "(except 电气化铁路牵引). 春(3-5) 峰 6-8、17-24 谷 2-5、9-15 平 0-2、5-6、8-9、15-17; 夏(6-8) 峰 6-8、18-24 (尖峰 18-21) "
    "谷 2-5、10-15 平 0-2、5-6、8-10、15-18; 秋(9-11) 峰 6-8、17-24 谷 2-5、11-15 平 0-2、5-6、8-11、15-17; 冬(12-2) 峰 6-8、16-23 "
    "(尖峰 17-20) 谷 2-5、10-15 平 23-2、5-6、8-10、15-16. 深谷 13-15 during 春节/劳动节/国庆节 (holiday rules for 2/5/10月; "
    "holiday dates per the national notice). 高峰 +60%, 低谷 −55%, 尖峰 = 高峰 +20%, 深谷 = 低谷 −20%.",
    "Float base = 当月平均购电价格 (one constant per month); 线损、系统运行费、输配电价、历史偏差电费折价、基金 do not float. "
    "尖峰/深谷 stored only where printed: up to 2026-04 尖峰 printed for 两部制 in 12、1月; from 2026-05 尖峰 printed for all rows in 6-8月 "
    "(and 12-2月), 深谷 printed in holiday months (5、10月); '—' cells are NULL.",
    "Source: www.sx.sgcc.com.cn 收费标准 column (c40145) – one-page image-only PDF posters; OCR'd with tesseract, cross-validated "
    "(component sum, monthly float base, info-table reads, voltage monotonicity, regulated prices by TDP period). "
    "2026-03 / 2026-04 are the 2026-04-03 '调整后' re-issues. 1.5倍 table not loaded.",
    "Layout: 3rd TDP period (to 2026-07) 7 rows (单一制 不满1/1-10（20）/35; 两部制 1-10（20）/35/110/220及以上); 4th period "
    "(2026-08 →, 发改价格〔2026〕1077号) 单一制 adds 110 and 220千伏及以上 (9 rows), 需量/容量 change.",
]
