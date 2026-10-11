"""国网天津市电力公司 代理购电工商业用户电价 — thin config over sgcc_cms (seed only) + ocr_table.

Quirks
* tj.sgcc.com.cn is unreachable from the pipeline network and from the Hark browser (ERR_EMPTY_RESPONSE), and
  fzgg.tj.gov.cn answers 403.  The monthly notices are taken from State Grid's 网上国网 portal 95598.cn ›
  信息公开 › 执行的电价和收费标准 (region 天津): each notice is ONE JPG poster (3543×7087, 2025-12: 4961×9921, heavy
  '国家电网' watermark) holding 3 tables: 代理购电工商业用户电价表 (loaded), 执行1.5倍代理购电价格 table (skipped),
  代理购电价格信息表 (read only to cross-check 代理购电价格 / 线损 / 系统运行费).  omg-static URLs collected by the
  browser into data/raw/tianjin/seed.json (live_list=False); files saved as data/raw/tianjin/YYYY-MM.jpg.
* Table: 10 rows (单一制 / 两部制 × 不满1千伏, 1-10千伏, 35千伏-110千伏(不含), 110千伏-220千伏(不含), 220千伏及以上),
  8-decimal prices (基金 0.02716875).  尖峰 column printed only in 1、2、7、8、12月 and only for 两部制 (大工业)
  rows; 单一制 cells are '-' (stored NULL).  No 深谷.
* OCR cross-validation with the notice's own arithmetic (津发改价管〔2025〕277号 as printed in note 3):
  非分时 = 代理购电 + 线损 + 输配 + 系统运行 + 基金 = 平段;  D = round(60%·(代理购电价格 + 电度输配电价), 6);
  高峰 = 平段 + D, 低谷 = 平段 − D;  尖峰 = 高峰 + round(20%·1.6·B, 6).  A cell is repaired only when ≥ 2 independent
  cells agree (ocr_table.solve_rows) and repaired TOU cells are snapped to the exact printed arithmetic above.
* 输配电价 / 需量 / 容量 are regulated: majority vote within a TDP period (3rd period to 2026-07, 4th from 2026-08).
"""
import os
import re

from . import sgcc_cms as cms
from . import ocr_table as ot
from .. import parse_common as pc

REGION = {"code": "tianjin", "name": "天津", "grid": "sgcc", "province": "天津市"}
CMS = cms.CMSRegion(REGION, "http://www.tj.sgcc.com.cn", "2774",
                    title_res=[r"国网天津市电力公司关于(\d{4})年(\d{1,2})月代理购电价格的公告"], max_pages=2,
                    timeout=15, live_list=False, file_pref=["jpg"])
_months = cms.months_between
policy_doc_row = CMS.policy_doc_row
TOU_ID_BASE = 12000
OCR_VERSION = 1
P4_FROM = "2026-08"     # 4th TDP period (发改价格〔2026〕1077号 / 津发改价管〔2026〕245号, cited in note 1 from 2026-08)

V5 = ["不满1千伏", "1-10千伏", "35千伏-110千伏(不含)", "110千伏-220千伏(不含)", "220千伏及以上"]
LAYOUT = [("单一制", v) for v in V5] + [("两部制", v) for v in V5]
COLS_BASE = ["energy_price", "purchase_price", "line_loss", "tdp", "system_op", "gov_fund"]
COLS_TAIL = ["peak", "flat", "valley", "demand_price", "capacity_price"]
INFO_ROWS = {"purchase_price": 4, "line_loss": 7, "system_op": 8}   # 序号 in 代理购电价格信息表
COEF = {"peak": 0.60, "valley": 0.60, "sharp_on_peak": 0.20}


def discover(start, end, log=print):
    anns = CMS.discover(start, end, log=log)
    for a in anns:   # omg-static URLs carry no extension: one poster per notice
        a.update(file_kind="jpg", file_ext="jpg")
        a.pop("img_urls", None)
    return anns


def download(ann, log=print):
    p = CMS._fetch_to(ann["file_url"], os.path.join(CMS.raw_dir, f"{ann['month']}.jpg"), [b"\xff\xd8"], log)
    ann["file_path"] = p
    ann["sha256"] = pc.sha256_file(p)
    return p


def _r6(x):
    return round(x + 1e-12, 6)


def _exact(r):
    """The printed arithmetic of the notice (8-decimal prices, 6-decimal float increments)."""
    B = r["purchase_price"] + r["tdp"]
    D = _r6(0.6 * B)
    f = r["energy_price"]
    e = {"flat": f, "peak": round(f + D, 8), "valley": round(f - D, 8)}
    if r.get("sharp") is not None:
        e["sharp"] = round(e["peak"] + _r6(0.32 * B), 8)
    return e


def _ocr(ann, log):
    img = ot.load_image(ann["file_path"])
    tables, a = ot.find_tables(img, thr=150)
    t = next((x for x in tables if len(x["hlines"]) >= 12 and x["top"] < img.height * 0.3), None)
    if t is None:
        raise RuntimeError(f"main table not found: {[(x['top'], len(x['hlines'])) for x in tables]}")
    # 执行时间 line just above the table
    h = int(img.height * 0.05)
    head = re.sub(r"\s+", "", ot._tess(img.crop((t["x0"], max(0, t["top"] - h), t["x1"], t["top"] - 2)), "chi_sim", psm=6))
    ann["ocr_title"] = head[:80]
    m = re.search(r"(\d{4})年(\d{1,2})月1日", head)
    if m and f"{int(m.group(1)):04d}-{int(m.group(2)):02d}" != ann["month"]:
        raise RuntimeError(f"执行时间 OCR says {m.group(0)}, expected {ann['month']}")
    scale = 2.0 if img.width < 4000 else 1.5
    tab = ot.read_table(img, t, a, scale=scale, log=log)
    if len(tab["data"]) != len(LAYOUT):
        raise RuntimeError(f"OCR: {len(tab['data'])} data rows, expected {len(LAYOUT)}")
    ncol = len(tab["data"][0][0])
    if ncol == 12:
        columns = COLS_BASE + ["sharp"] + COLS_TAIL
    elif ncol == 11:
        columns = COLS_BASE + COLS_TAIL
    else:
        raise RuntimeError(f"OCR: {ncol} numeric columns (expected 11 or 12)")
    info = {}
    try:
        others = [x for x in tables if x is not t and len(x["hlines"]) >= 12]
        iv = ot.read_info_table(img, others[-1:], a, scale=scale, log=log) if others else {}
        info = {f: iv[k] for f, k in INFO_ROWS.items() if k in iv}
        ann["ocr_info_table"] = {f: v[1] for f, v in info.items()}
    except RuntimeError as e:
        log(f"  info table: {e}")
    rows, fixes = ot.solve_rows(tab, columns, LAYOUT, ann["month"], COEF, base_kind="P+tdp", tol=1.2e-6,
                                log=log, info=info)
    # snap repaired TOU cells to the exact printed arithmetic (solve_rows derives without the 6-decimal rounding)
    for r in rows:
        r["category"] = "工商业用电"
        if r["tariff_type"] == "单一制":
            r["demand_price"] = r["capacity_price"] = None
            if "sharp" in r and r["sharp"] is not None:
                fixes.append(f"单一制 {r['voltage']} sharp: OCR {r['sharp']} -> NULL (cell printed '-')")
            r["sharp"] = None
        else:
            r.setdefault("sharp", None)
        r.setdefault("deep_valley", None)
        try:
            exp = _exact(r)
        except TypeError:
            continue
        for f, v in exp.items():
            if r.get(f) is not None and r[f] != v and abs(r[f] - v) <= 1.2e-6:
                fixes.append(f"{r['tariff_type']} {r['voltage']} {f}: {r[f]} -> {v} (snap to printed arithmetic)")
                r[f] = v
    for r in rows:
        for f in ("energy_price", "purchase_price", "tdp", "peak", "flat", "valley"):
            v = r.get(f)
            if v is None or not (0 < v < 3):
                raise RuntimeError(f"OCR: implausible {f}={v} for {r['tariff_type']} {r['voltage']}")
        for f in ("line_loss", "gov_fund", "system_op"):
            v = r.get(f)
            if v is None or not (-0.3 < v < 0.5):
                raise RuntimeError(f"OCR: implausible {f}={v}")
    bad = [x for x in pc.validate_rows(rows) + extra_validate(rows)
           if x["check"] in ("components_sum", "components_missing", "tou_formula", "tou_order")]
    if bad:
        raise RuntimeError(f"OCR unreliable: {len(bad)} formula failures, e.g. {bad[0]['check']} {bad[0]['detail']}")
    ann["ocr_corrections"] = fixes
    ann["ocr_voltage_digits"] = tab["voltage_digits"]
    ann["parse_method"] = f"OCR (tesseract ×{scale}) of 95598.cn JPG poster + formula cross-check ({len(fixes)} repairs)"
    ann["effective_from"], ann["effective_to"] = cms.effective_dates(head, ann["month"])
    return rows


def parse(ann, template=None, log=print):
    rows = ot.cached_parse(ann, REGION["code"], lambda: _ocr(ann, log), version=OCR_VERSION)
    if any(r["tariff_type"] == "两部制" and (r.get("demand_price") is None or r.get("capacity_price") is None)
           for r in rows):
        fx = _reread_blank(ann, rows)
        if fx:
            ann["ocr_corrections"] = list(ann.get("ocr_corrections") or []) + fx
    return rows


def _reread_blank(ann, rows):
    """Short regulated numbers ('27.0') are sometimes dropped by the line OCR: re-read blank 两部制 需量/容量 cells
    one by one (×3, digit whitelist). Geometry: the last two columns and the last 10 row bands of the main table."""
    img = ot.load_image(ann["file_path"])
    tables, a = ot.find_tables(img, thr=150)
    t = next((x for x in tables if len(x["hlines"]) >= 12 and x["top"] < img.height * 0.3), None)
    if t is None:
        return []
    xs = ot.vlines(a, t)
    cols = [(x0, x1) for x0, x1 in zip(xs, xs[1:]) if x1 - x0 > 8]
    ys = t["hlines"]
    bands = [(y0, y1) for y0, y1 in zip(ys, ys[1:]) if y1 - y0 > 8][-len(LAYOUT):]
    if len(bands) != len(LAYOUT) or len(cols) < 2:
        return []
    fixes = []
    for k, r in enumerate(rows):
        if r["tariff_type"] != "两部制":
            continue
        for f, (x0, x1) in (("demand_price", cols[-2]), ("capacity_price", cols[-1])):
            if r.get(f) is not None:
                continue
            y0, y1 = bands[k]
            cell = img.crop((x0 + 4, y0 + 4, x1 - 4, y1 - 4))
            cell = cell.resize((cell.width * 3, cell.height * 3))
            txt = ot._tess(cell, "eng", psm=7, extra="-c tessedit_char_whitelist=0123456789.")
            if re.fullmatch(r"\d{2}\.\d", txt or ""):
                r[f] = float(txt)
                fixes.append(f"两部制 {r['voltage']} {f}: blank in line OCR, cell re-read '{txt}'")
    return fixes


def _period(m, rows=None):
    return "P4" if m >= P4_FROM else "P3"


def postprocess(parsed, anns):
    """Regulated prices: majority vote within a TDP period (replaces disagreeing reads), then fill 两部制 需量/容量
    cells the line OCR left blank with the period's majority value (logged)."""
    from collections import Counter
    log = ot.regulated_consensus(parsed, anns, fields=("tdp", "demand_price", "capacity_price"), period_key=_period)
    groups = {}
    for m, rows in parsed.items():
        for r in rows:
            if r["tariff_type"] == "两部制":
                for f in ("demand_price", "capacity_price"):
                    groups.setdefault((_period(m), r["voltage"], f), []).append((m, r))
    for (pk, v, f), items in groups.items():
        cnt = Counter(r[f] for _, r in items if r.get(f) is not None)
        if not cnt:
            continue
        val, n = cnt.most_common(1)[0]
        if n < 2 or n <= sum(cnt.values()) / 2:
            continue
        for m, r in items:
            if r.get(f) is None:
                msg = f"两部制 {v} {f}: blank in OCR -> {val} (regulated, {n} months of {pk} agree)"
                anns[m].setdefault("ocr_corrections", []).append(msg)
                log.append(f"{m} {msg}")
                r[f] = val
    return log


def extra_validate(rows):
    fails = cms.tou_formula_check(rows, _exact, tol=5e-9)
    for m in sorted({r["month"] for r in rows}):
        for tt in ("单一制", "两部制"):
            g = sorted([r for r in rows if r["month"] == m and r["tariff_type"] == tt], key=lambda r: r["voltage_rank"])
            for lo, hi in zip(g, g[1:]):
                if not hi["tdp"] < lo["tdp"]:
                    fails.append({"month": m, "category": hi["category"], "tariff_type": tt, "voltage": hi["voltage"],
                                  "check": "tdp_order", "detail": f"tdp {hi['tdp']} !< {lo['tdp']}"})
    return fails


# ---------------------------------------------------------------- TOU (津发改价管〔2025〕277号)
TOU_277 = ("https://www.95598.cn/omg-static//omg-static/99309111418199724687701554929817.pdf",
           "天津市发展和改革委员会关于优化我市峰谷分时电价机制有关事项的通知", "津发改价管〔2025〕277号",
           "2025-08-06", "2025-10-01")


def tou_rules():
    code = REGION["code"]
    d = cms.tou_doc(code, *TOU_277)
    summer = {"peak": [(15, 23)], "flat": [(0, 1), (9, 15), (23, 24)], "valley": [(1, 9)]}
    other = {"peak": [(8, 10), (16, 22)], "flat": [(6, 8), (10, 12), (14, 16), (22, 24)], "valley": [(0, 6), (12, 14)]}
    rules = [([7, 8], "normal", cms.segs_from_hours(dict(summer, sharp=[(20, 22)]))),
             ([1, 2, 12], "normal", cms.segs_from_hours(dict(other, sharp=[(18, 20)]))),
             ([3, 4, 5, 6, 9, 10, 11], "normal", cms.segs_from_hours(other))]
    return d, cms.tou_rows(code, d, rules, TOU_ID_BASE)


TOU_NOTES = [
    "TOU periods per 天津市发展改革委《关于优化我市峰谷分时电价机制有关事项的通知》津发改价管〔2025〕277号 (dated 2025-08-06, "
    "effective 2025-10-01; cited in note 3 of every 2025-11..2026-10 notice). 7、8月: 峰 15-23 (尖峰 20-22), 平 0-1、9-15、23-24, "
    "谷 1-9. Other months: 峰 8-10、16-22 (尖峰 18-20 in 1、2、12月), 平 6-8、10-12、14-16、22-24, 谷 0-6、12-14. No holiday variant, no 深谷.",
    "Float: 峰/谷 = 平段 ± 60%·(代理购电价格 + 电度输配电价); 尖峰 = 峰段 + 20% of the floated peak (= 平段 + 92%·B). 线损、"
    "系统运行费、基金、基本电费 do not float. 尖峰 applies to 大工业 (两部制) only: the notice prints it only for 两部制 rows in "
    "1、2、7、8、12月 and it is stored only where printed.",
    "Source: State Grid 95598.cn static copies (网上国网 › 执行的电价和收费标准, region 天津) of the monthly JPG poster; OCR'd with "
    "tesseract and cross-validated against the poster's own arithmetic (Σ components, ±60% float, tdp ordering, 信息表 reads); "
    "repairs logged in announcements.json ocr_corrections. 1.5倍 and 信息表 tables not loaded.",
]
