"""国网吉林省电力有限公司 代理购电工商业用户电价 — seed (95598.cn) + text PDF / OCR of JPG.

Quirks
* jl.sgcc.com.cn answers HTTP 412 (JS anti-bot) to curl and its 收费标准 column only carries sporadic months.  The
  monthly notices ("国网吉林省电力有限公司YYYY年M月代理购电工商业用户电价") come from State Grid's 网上国网 portal 95598.cn ›
  信息公开 › 执行的电价和收费标准 (partNo=P2019, region 吉林); omg-static URLs collected once with a browser into
  data/raw/jilin/seed.json (curl downloads them).
* Format varies by month: 2025-11..2026-05 and 2026-08 are 2-page text PDFs (p1 = 代理购电工商业用户电价表, p2 = 价格信息表);
  2026-06, 2026-07, 2026-09, 2026-10 are one clean computer-rendered JPG (2245 px wide) holding the same two tables →
  OCR (ocr_table), cross-validated with the notice's own arithmetic: 电度用电价格 = 平段 = Σ components;
  高峰/低谷 = 平段 ± 55%·代理购电价格 (only the purchase price floats; 7-decimal results), and the regulated 输配/需量/容量
  prices by majority within the TDP period (text months included).
* Column order (by header text): 电度用电价格, 代理购电价格, 上网环节线损电价, 系统运行费用折价, 电度输配电价, 政府性基金及附加,
  尖峰/高峰/平/低谷, 最大需量, 变压器容量.  尖峰 is always '-' (灵活启动 since 吉发改价格〔2025〕11号) → NULL; no 深谷.
* Layout: 3rd TDP period (to 2026-07) 单一制 不满1 / 1-10 / 66千伏 + 两部制 1-10 / 66 / 220及以上 = 6 rows; 4th period
  (2026-08 →, 吉发改价格〔2026〕399号 / 发改价格〔2026〕1077号) 单一制 adds 220千伏及以上 → 7 rows.
"""
import json, os, re

from . import sgcc_cms as cms
from . import ocr_table as ocr
from .. import parse_common as pc

REGION = {"code": "jilin", "name": "吉林", "grid": "sgcc", "province": "吉林省"}
RAW = os.path.join(pc.DATA, "raw", REGION["code"])
TOU_ID_BASE = 44000
OCR_VERSION = 2
_months = cms.months_between

COLUMNS = ["energy_price", "purchase_price", "line_loss", "system_op", "tdp", "gov_fund",
           "sharp", "peak", "flat", "valley", "demand_price", "capacity_price"]
LAYOUTS = {6: [("单一制", "不满1千伏"), ("单一制", "1-10千伏"), ("单一制", "66千伏"),
               ("两部制", "1-10千伏"), ("两部制", "66千伏"), ("两部制", "220千伏及以上")],
           7: [("单一制", "不满1千伏"), ("单一制", "1-10千伏"), ("单一制", "66千伏"), ("单一制", "220千伏及以上"),
               ("两部制", "1-10千伏"), ("两部制", "66千伏"), ("两部制", "220千伏及以上")]}
INFO_ROWS = {"purchase_price": 4, "line_loss": 7, "system_op": 8}
UP = 0.55


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
def parse(ann, template=None, log=print):
    if ann["file_ext"] == "pdf":
        return _parse_pdf(ann)
    return ocr.cached_parse(ann, REGION["code"], lambda: _parse_img(ann, log), OCR_VERSION)


def _parse_pdf(ann):
    for i, text, tables in cms.pdf_tables(ann["file_path"]):
        t = re.sub(r"\s+", "", text)
        if "代理购电工商业用户电价表" in t and tables:
            ef, et = cms.effective_dates(text, ann["month"])
            if ef[:7] != ann["month"]:
                raise RuntimeError(f"PDF is for {ef[:7]}, expected {ann['month']}")
            ann["effective_from"], ann["effective_to"] = ef, et
            rows = cms.parse_grid(tables[0], ann["month"], nd=7)
            for r in rows:
                r["category"] = "工商业用电"
            ann["parse_method"] = f"pdfplumber page {i + 1}"
            return rows
    raise RuntimeError("代理购电工商业用户电价表 page not found")


def _preclean(tab):
    """'—' read as a short digit string → blank; dropped decimal point in a price ('0842619' → 0.842619)."""
    fixes = []
    for i, (nums, raws) in enumerate(tab["data"]):
        for j, raw in enumerate(raws):
            f = COLUMNS[j] if j < len(COLUMNS) else None
            if not raw or "." in raw or f is None:
                continue
            if f in ("demand_price", "capacity_price"):
                if re.fullmatch(r"\d{3}", raw):           # 384 → 38.4 (the 容量 prices are integers: 24, 23)
                    new = raw[:2] + "." + raw[2]
                    fixes.append(f"row {i + 1} {f}: OCR '{raw}' -> {new} (decimal point dropped)")
                    nums[j] = float(new); raws[j] = new
                continue
            if re.fullmatch(r"0\d{4,7}", raw):
                new = "0." + raw[1:]
                fixes.append(f"row {i + 1} {f}: OCR '{raw}' -> {new} (decimal point dropped)")
                nums[j] = float(new); raws[j] = new
            elif len(raw) < 4:
                fixes.append(f"row {i + 1} {f}: OCR '{raw}' -> blank ('-' cell)")
                nums[j] = None; raws[j] = ""
    return fixes


def _parse_img(ann, log=print):
    from PIL import Image
    img = Image.open(ann["file_path"]).convert("L")
    if img.width < 3000:      # small clean render: upscale for tesseract
        img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
    tables, a = ocr.find_tables(img, thr=150)
    t = None
    for tb in tables:
        if len(tb["hlines"]) < 8:
            continue
        tb["title_ocr"] = ocr.title_above(img, tb, height=int((tb["hlines"][1] - tb["hlines"][0]) * 4))
        if "信息" not in tb["title_ocr"] and "1.5" not in tb["title_ocr"]:
            t = tb
            break
    if t is None:
        raise RuntimeError(f"tariff table not found: {[x.get('title_ocr', '')[:30] for x in tables]}")
    tab = ocr.read_table(img, t, a, scale=1.0, log=log)
    pre = _preclean(tab)
    ncol, nrow = len(tab["data"][0][0]), len(tab["data"])
    if ncol != len(COLUMNS) or nrow not in LAYOUTS:
        raise RuntimeError(f"unexpected table shape {nrow} rows × {ncol} numeric columns")
    info = {}
    try:
        iv = ocr.read_info_table(img, [x for x in tables if x is not t], a, scale=1.0, want=("信息表", "价格信息"), log=log)
        info = {f: iv[k] for f, k in INFO_ROWS.items() if k in iv}
    except RuntimeError:
        pass
    rows, fixes = ocr.solve_rows(tab, COLUMNS, LAYOUTS[nrow], ann["month"], {"peak": UP, "valley": UP},
                                 base_kind="month", tol=2e-6, log=log, info=info)
    fixes = pre + fixes + _snap(rows)
    for r in rows:
        r["category"] = "工商业用电"
        r["sharp"] = None                         # '-' on every notice (尖峰 灵活启动)
        if r["tariff_type"] == "单一制":
            r["demand_price"] = r["capacity_price"] = None
    bad = [f for f in pc.validate_rows(rows) if f["check"] in ("components_sum", "components_missing", "tou_order")]
    if bad:
        raise RuntimeError(f"OCR unreliable: {len(bad)} failures, e.g. {bad[0]['detail']}")
    ann["effective_from"], ann["effective_to"] = cms.effective_dates("", ann["month"])
    ann["parse_method"] = "ocr(tesseract eng, JPG ×2 upscale)"
    ann["ocr_corrections"] = fixes
    return rows


def _snap(rows):
    """峰/谷 = 平段 ± D where D (≈ 0.55·P, rounded per column on the notice) is identical on every row of the month:
    snap cells re-derived by solve_rows (≈1e-7 off) to 平段 + the column's modal D."""
    from collections import Counter
    fixes = []
    for f in ("peak", "valley"):
        ds = Counter(round(r[f] - r["flat"], 7) for r in rows if r.get(f) is not None and r.get("flat") is not None)
        if not ds:
            continue
        D, n = ds.most_common(1)[0]
        if n < 2:
            continue
        for r in rows:
            if r.get(f) is None or r.get("flat") is None:
                continue
            exp = round(r["flat"] + D, 7)
            if r[f] != exp and abs(r[f] - exp) <= 3e-6:
                fixes.append(f"{r['tariff_type']} {r['voltage']} {f}: {r[f]} -> {exp} (平段 + modal D {D})")
                r[f] = exp
    return fixes


def _period(m, rows):
    return "P4" if m >= "2026-08" else "P3"


def postprocess(parsed, anns):
    from ._north_util import consensus_ocr_only
    return consensus_ocr_only(parsed, anns, ("tdp", "demand_price", "capacity_price"),
                              lambda m: "P4" if m >= "2026-08" else "P3", lambda a: a.get("file_ext") == "jpg")


def extra_validate(rows):
    def exp(r):
        D = round(UP * r["purchase_price"] + 1e-12, 7)
        return {"flat": r["energy_price"], "peak": r["flat"] + D, "valley": r["flat"] - D}
    return cms.tou_formula_check(rows, exp, tol=1.5e-7)


# ---------------------------------------------------------------- TOU (吉发改价格〔2025〕11号)
TOU_11 = ("https://www.95598.cn/omg-static/99305070811275452078400030458563.pdf",
          "吉林省发展改革委关于进一步优化分时电价政策的通知", "吉发改价格〔2025〕11号", "2025-01-16", "2025-04-01")


def tou_rules():
    code = REGION["code"]
    d = cms.tou_doc(code, *TOU_11)
    spec = {"peak": [(8, 10), (16, 21)], "flat": [(5, 8), (10, 11), (14, 16), (21, 23)],
            "valley": [(0, 5), (11, 14), (23, 24)]}
    rules = [(list(range(1, 13)), "normal", cms.segs_from_hours(spec))]
    return d, cms.tou_rows(code, d, rules, TOU_ID_BASE)


TOU_NOTES = [
    "TOU per 吉发改价格〔2025〕11号 (2025-01-16; effective 2025-02-01, for 代理购电 users from 2025-04-01; cited in note 3 of every "
    "notice 2025-11..2026-10). Source: scanned official notice as published on 95598.cn (copy web/downloads/jilin_tou_2025_11.pdf). "
    "One rule all year: 高峰 8-10、16-21, 平 5-8、10-11、14-16、21-23, 低谷 0-5、11-14、23-24. No seasonal/holiday variant, no 深谷.",
    "高峰/低谷 = 平段 ± 55%, only the 上网电价/代理购电价格 floats (输配、系统运行费、基金、线损 do not). 尖峰 is no longer fixed: "
    "'灵活启动' by the grid with DRC approval, announced ad hoc – so no 尖峰 rule and the 尖峰 column is NULL.",
    "Scope: 工商业 users with equipment capacity ≥100千伏安 (others, and 铁路/医院/政府机关 etc., may opt in).",
    "Source: 95598.cn (网上国网) 执行的电价和收费标准, region 吉林. 2025-11..2026-05 and 2026-08 are text PDFs (pdfplumber); "
    "2026-06/07/09/10 are JPG renders, OCR'd with tesseract and cross-validated (component sum, 平 ± 0.55·P, regulated prices by period).",
]
