"""国网内蒙古东部电力有限公司（蒙东）代理购电工商业用户电价 — seed + text PDF / OCR.

Quirks
* md.sgcc.com.cn answers HTTP 412 (JS anti-bot) to curl.  Its column 信息公开 › 政策法规及收费标准
  (http://www.md.sgcc.com.cn/html/md/gb/xxgkx/zcfgjsfbz/index.shtml) carries every monthly 代理购电价格公告; each article
  is ONE inline scan (2245 px wide, 附件1 电价表 + 1.5倍 table + 附件2 信息表) under /u/cms/md/picture/YYYYMM/<hash>.jpg,
  which curl downloads fine.  On 95598.cn (P2019, region 内蒙 › 本级) only 2026-07..2026-10 are listed; those PDFs are
  used for 2026-07..10 (07/08 image-only, 09/10 with a text layer).  URLs in data/raw/mengdong/seed.json (browser-collected).
* Float (内发改价费字〔2023〕1631号, restated in note 2): only the 代理购电价格 P floats; 高峰 = 平 + 68%·P, 低谷 = 平 − 52%·P,
  尖峰 = 高峰 + 20%·1.68·P, 深谷 = 低谷 − 20%·0.48·P (6 decimals).  Used for OCR cross-validation only; 尖峰/深谷 stored where
  printed (6-8月 columns), '-' / blank → NULL.
* Layout: 3rd TDP period (to 2026-07) 单一制 不满1 / 1-10 / 35 + 两部制 1-10 / 35 / 110 / 220及以上 = 7 rows;
  4th period (2026-08 →) both classes × 不满1 / 1-10(20) / 35 / 110（66） / 220及以上 = 10 rows.
  Columns: 非分时, 代理购电价格, 线损, 输配, 基金, 系统运行费, [尖峰], 高峰, 平, 低谷, [深谷], 需量, 容量 (11 or 13).
"""
import json, os, re
from collections import Counter

from . import sgcc_cms as cms
from . import ocr_table as ocr
from .. import parse_common as pc

REGION = {"code": "mengdong", "name": "蒙东", "grid": "sgcc", "province": "内蒙古自治区（东部）"}
RAW = os.path.join(pc.DATA, "raw", REGION["code"])
TOU_ID_BASE = 45000
OCR_VERSION = 1
_months = cms.months_between

BASE = ["energy_price", "purchase_price", "line_loss", "tdp", "gov_fund", "system_op"]
COLUMNS = {11: BASE + ["peak", "flat", "valley", "demand_price", "capacity_price"],
           13: BASE + ["sharp", "peak", "flat", "valley", "deep_valley", "demand_price", "capacity_price"]}
P3 = [("单一制", "不满1千伏"), ("单一制", "1-10千伏"), ("单一制", "35千伏"),
      ("两部制", "1-10千伏"), ("两部制", "35千伏"), ("两部制", "110千伏"), ("两部制", "220千伏及以上")]
V4 = ["不满1千伏", "1-10(20)千伏", "35千伏", "110(66)千伏", "220千伏及以上"]
LAYOUTS = {7: P3, 10: [("单一制", v) for v in V4] + [("两部制", v) for v in V4]}
COEF = {"peak": 0.68, "valley": 0.52, "sharp_on_peak": 0.20, "deep_on_valley": 0.20}
INFO_ROWS = {"purchase_price": 4, "line_loss": 7, "system_op": 8}


def discover(start, end, log=print):
    seed = json.load(open(os.path.join(RAW, "seed.json"), encoding="utf-8"))
    wanted = set(_months(start, end))
    out = [dict(a, discovered_via="seed", file_kind=a["file_ext"]) for a in seed["articles"] if a["month"] in wanted]
    log(f"  seed: {len(out)} months")
    return sorted(out, key=lambda a: a["month"])


MAGIC = {"pdf": b"%PDF", "jpg": b"\xff\xd8"}


def download(ann, log=print):
    path = os.path.join(RAW, f"{ann['month']}.{ann['file_ext']}")
    if not (os.path.exists(path) and os.path.getsize(path) > 1000):
        r = pc.http_get(ann["file_url"], timeout=90)
        if not r.content.startswith(MAGIC[ann["file_ext"]]):
            raise RuntimeError(f"unexpected file type: {ann['file_url']}")
        open(path, "wb").write(r.content)
        log(f"  downloaded {os.path.relpath(path, pc.ROOT)}")
    ann["file_path"] = path
    ann["sha256"] = pc.sha256_file(path)
    return path


def policy_doc_row(ann):
    return {"id": pc.doc_id(ann["file_url"]), "region_code": REGION["code"], "kind": "agency_price",
            "title": ann["title"], "doc_no": None, "publish_date": ann.get("publish_date"),
            "effective_from": ann.get("effective_from") or f"{ann['month']}-01", "effective_to": ann.get("effective_to"),
            "source_url": ann["file_url"], "file_path": os.path.relpath(ann["file_path"], pc.ROOT),
            "sha256": ann["sha256"], "fetched_at": pc.now_iso()}


# ---------------------------------------------------------------- parse
def _text_layer(path):
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        return "".join((p.extract_text() or "") for p in pdf.pages).strip()


def _volt(v):
    return v.replace("以上上", "以上")


def parse(ann, template=None, log=print):
    if ann["file_ext"] == "pdf" and _text_layer(ann["file_path"]):
        return _parse_pdf(ann)
    rows = ocr.cached_parse(ann, REGION["code"], lambda: _parse_img(ann, log), OCR_VERSION)
    return rows


def _parse_pdf(ann):
    for i, text, tables in cms.pdf_tables(ann["file_path"]):
        if "代理购电工商业用户电价表" in re.sub(r"\s+", "", text) and tables:
            ef, et = cms.effective_dates(text, ann["month"])
            if ef[:7] != ann["month"]:
                raise RuntimeError(f"PDF is for {ef[:7]}, expected {ann['month']}")
            ann["effective_from"], ann["effective_to"] = ef, et
            rows = cms.parse_grid(tables[0], ann["month"], voltage_fn=_volt)
            for r in rows:
                r["category"] = "工商业用电"
            ann["parse_method"] = f"pdfplumber page {i + 1} (first table)"
            return rows
    raise RuntimeError("代理购电工商业用户电价表 not found")


def _preclean(tab, cols):
    fixes = []
    for i, (nums, raws) in enumerate(tab["data"]):
        for j, raw in enumerate(raws):
            f = cols[j] if j < len(cols) else None
            if not raw or "." in raw or f is None:
                continue
            if f in ("demand_price", "capacity_price"):
                if re.fullmatch(r"\d{3}", raw):
                    new = raw[:2] + "." + raw[2]
                    fixes.append(f"row {i + 1} {f}: OCR '{raw}' -> {new} (decimal point dropped)")
                    nums[j] = float(new); raws[j] = new
                continue
            if re.fullmatch(r"0\d{4}|0\d{6}", raw):          # 4-decimal tdp / 6-decimal prices
                new = "0." + raw[1:]
                fixes.append(f"row {i + 1} {f}: OCR '{raw}' -> {new} (decimal point dropped)")
                nums[j] = float(new); raws[j] = new
            elif len(raw) < 4:
                fixes.append(f"row {i + 1} {f}: OCR '{raw}' -> blank ('-' / empty cell)")
                nums[j] = None; raws[j] = ""
    return fixes


def _snap(rows):
    """TOU cells = 平段 + D with D identical on every row of the month (P is one number): snap re-derived cells."""
    fixes = []
    for f in ("peak", "valley", "sharp", "deep_valley"):
        ds = Counter(round(r[f] - r["flat"], 6) for r in rows if r.get(f) is not None and r.get("flat") is not None)
        if not ds:
            continue
        D, n = ds.most_common(1)[0]
        if n < 2:
            continue
        for r in rows:
            if r.get(f) is None or r.get("flat") is None:
                continue
            exp = round(r["flat"] + D, 6)
            if r[f] != exp and abs(r[f] - exp) <= 3e-6:
                fixes.append(f"{r['tariff_type']} {r['voltage']} {f}: {r[f]} -> {exp} (snapped to 平段 + D {D})")
                r[f] = exp
    return fixes


def _parse_img(ann, log=print):
    from PIL import Image
    img = ocr.load_image(ann["file_path"])
    if img.width < 3000:
        img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
    tables, a = ocr.find_tables(img, thr=150)
    t = None
    for tb in tables:
        if len(tb["hlines"]) < 8:
            continue
        tb["title_ocr"] = ocr.title_above(img, tb, height=int((tb["hlines"][1] - tb["hlines"][0]) * 4))
        if not any(x in tb["title_ocr"] for x in ("1.5", "倍", "信息")):
            t = tb
            break
    if t is None:
        raise RuntimeError(f"tariff table not found: {[x.get('title_ocr', '')[:30] for x in tables]}")
    tab = ocr.read_table(img, t, a, scale=1.0, log=log)
    ncol, nrow = len(tab["data"][0][0]), len(tab["data"])
    if ncol not in COLUMNS or nrow not in LAYOUTS:
        raise RuntimeError(f"unexpected table shape {nrow} rows × {ncol} numeric columns")
    cols = COLUMNS[ncol]
    pre = _preclean(tab, cols)
    info = {}
    try:
        iv = ocr.read_info_table(img, [x for x in tables if x is not t], a, scale=1.0, want=("信息表", "价格信息"), log=log)
        info = {f: iv[k] for f, k in INFO_ROWS.items() if k in iv}
    except RuntimeError:
        pass
    rows, fixes = ocr.solve_rows(tab, cols, LAYOUTS[nrow], ann["month"], COEF, base_kind="month", tol=2e-6,
                                 log=log, info=info)
    fixes = pre + fixes + _snap(rows)
    for r in rows:
        r["category"] = "工商业用电"
        if r["tariff_type"] == "单一制":
            r["demand_price"] = r["capacity_price"] = None
    bad = [f for f in pc.validate_rows(rows) if f["check"] in ("components_sum", "components_missing", "tou_order")]
    if bad:
        raise RuntimeError(f"OCR unreliable: {len(bad)} failures, e.g. {bad[0]['detail']}")
    ann["effective_from"], ann["effective_to"] = cms.effective_dates("", ann["month"])
    ann["parse_method"] = "ocr(tesseract eng, scan ×2 upscale)"
    ann["ocr_corrections"] = fixes
    return rows


def _period(m, rows):
    return "P4" if m >= "2026-08" else "P3"


def postprocess(parsed, anns):
    from ._north_util import consensus_ocr_only
    return consensus_ocr_only(parsed, anns, ("tdp", "demand_price", "capacity_price"),
                              lambda m: "P4" if m >= "2026-08" else "P3", lambda a: "ocr" in (a.get("parse_method") or ""))


def extra_validate(rows):
    def exp(r):
        P, f = r["purchase_price"], r["flat"]
        e = {"flat": r["energy_price"], "peak": f + 0.68 * P, "valley": f - 0.52 * P}
        if r.get("sharp") is not None:
            e["sharp"] = f + 0.68 * P + 0.2 * 1.68 * P
        if r.get("deep_valley") is not None:
            e["deep_valley"] = f - 0.52 * P - 0.2 * 0.48 * P
        return e
    return cms.tou_formula_check(rows, exp, tol=1.5e-6)


# ---------------------------------------------------------------- TOU (内发改价费字〔2023〕1631号)
TOU_1631 = ("https://www.95598.cn/omg-static/99308051060403522315301243350380",
            "内蒙古自治区发展和改革委员会关于完善蒙东电网工商业分时电价政策的通知（试行）", "内发改价费字〔2023〕1631号",
            "2023-12-28", "2024-02-01")
SPEC = {"peak": [(6, 9), (17, 22)], "flat": [(5, 6), (9, 11), (14, 17), (22, 24)], "valley": [(11, 14), (0, 5)]}


def tou_rules():
    code = REGION["code"]
    d = cms.tou_doc(code, *TOU_1631)
    summer = dict(SPEC, sharp=[(18, 20)], deep_valley=[(12, 14)])
    rules = [([1, 2, 3, 4, 5, 9, 10, 11, 12], "normal", cms.segs_from_hours(SPEC)),
             ([6, 7, 8], "normal", cms.segs_from_hours(summer))]
    return d, cms.tou_rows(code, d, rules, TOU_ID_BASE)


TOU_NOTES = [
    "TOU per 内发改价费字〔2023〕1631号 《关于完善蒙东电网工商业分时电价政策的通知（试行）》 as restated in note 2 of every notice "
    "2025-11..2026-10 (official scan on 95598.cn, copy web/downloads/mengdong_tou_1631.pdf; scope clarified by 内发改价费字〔2024〕143号, "
    "web/downloads/mengdong_tou_143.pdf): 峰 6-9、17-22, 平 5-6、9-11、14-17、22-24, 谷 0-5、11-14; 6-8月 尖峰 18-20 and 深谷 12-14.",
    "Float on the 代理购电价格 only: 高峰 +68%, 低谷 −52% (of 平段), 尖峰 = 高峰 +20%, 深谷 = 低谷 −20%.",
    "Sources: 2025-11..2026-06 = inline scans on www.md.sgcc.com.cn (信息公开›政策法规及收费标准), 2026-07..10 = 95598.cn PDFs; "
    "scans OCR'd with tesseract and cross-validated (component sum, 平 ± k·P, regulated prices by TDP period). 1.5倍 table not loaded.",
    "Layout: 3rd TDP period 7 rows (单一制 不满1/1-10/35; 两部制 1-10/35/110/220及以上); 4th period (2026-08 →) 10 rows "
    "(单一制/两部制 × 不满1/1-10(20)/35/110(66)/220及以上).",
]
