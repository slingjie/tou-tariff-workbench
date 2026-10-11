"""国网湖北省电力有限公司 代理购电工商业用户电价 (湖北) — 95598.cn PDFs + OCR.

Quirks
* hb.sgcc.com.cn is unreachable; 95598.cn › 执行的电价和收费标准 (region 湖北) carries combined notices
  "国网湖北省电力有限公司2025年1-12月代理购电" (12 pages, one month per page in reverse order → 2025-12 = page 1, 2025-11 = page 2) and
  "…2026年1-6月代理购电" (6 pages, page 1 = 2026-06 … page 6 = 2026-01), then monthly "…2026年M月代理购电" from 2026-07.  All are image-only PDFs (one scanned
  bitmap per page, 2245-3367 px) → OCR.  policy_doc.source_url carries '#page=N' for the combined files.
* Table 国网湖北省电力有限公司代理购电工商业用户电价表, 12 numeric columns: 非分时电度电价, 代理购电价格, 上网环节线损费用,
  电度输配电价, 政府性基金及附加, 系统运行费折价, 尖峰, 高峰, 平时段 (9=1), 低谷, 最大需量, 变压器容量.  尖峰 is printed for
  every month (鄂发改价管〔2024〕77号: 尖峰 every month, 7-8月 20-22, other months 18-20).  Prices are printed with 6 decimals (tdp 4) in every month of the window
  (pages for 2025-01..10 of the combined 2025 file use 4 decimals, outside the window).
* Float: base = 代理购电价格 + 线损 (电度电价 minus 基金, 输配电价, 系统运行费); TOU − energy is the same constant on every row
  → OCR cross-check = _central_util.affine_solve.
* Layout: to 2026-07 7 rows (单一制 不满1千伏 / 1~10(20)千伏 / 35千伏, 两部制 1~10(20) / 35 / 110 / 220千伏及以上);
  from 2026-08 (4th TDP period) 9 rows (单一制 不满1 / 1~10(20) / 35 / 110 / 220千伏及以上, 两部制 1~10(20) / 35 / 110 / 220).
  Layout is chosen by the number of data rows OCR finds and checked against the tdp ordering.
"""
import re

from . import _central_util as cu
from . import ocr_table as ot
from . import sgcc_cms as cms
from .. import parse_common as pc

REGION = {"code": "hubei", "name": "湖北", "grid": "sgcc", "province": "湖北省"}
CODE = REGION["code"]
OCR_VERSION = 1
TOU_ID_BASE = 23000
_months = cu.pc_months
COLS = ["energy_price", "purchase_price", "line_loss", "tdp", "gov_fund", "system_op", "sharp", "peak", "flat",
        "valley", "demand_price", "capacity_price"]
SHARED = ["purchase_price", "line_loss", "gov_fund", "system_op"]
LAYOUTS = {
    7: [("单一制", "不满1千伏"), ("单一制", "1-10(20)千伏"), ("单一制", "35千伏"),
        ("两部制", "1-10(20)千伏"), ("两部制", "35千伏"), ("两部制", "110千伏"), ("两部制", "220千伏及以上")],
    9: [("单一制", "不满1千伏"), ("单一制", "1-10(20)千伏"), ("单一制", "35千伏"), ("单一制", "110千伏"),
        ("单一制", "220千伏及以上"), ("两部制", "1-10(20)千伏"), ("两部制", "35千伏"), ("两部制", "110千伏"),
        ("两部制", "220千伏及以上")],
}


def discover(start, end, log=print):
    return cu.seed_discover(CODE, start, end, log)


def download(ann, log=print):
    return cu.download(CODE, ann, log)


def policy_doc_row(ann):
    return cu.policy_doc_row(CODE, ann)


def _ocr(ann, log):
    img = cu.page_image(ann["file_path"], page=ann.get("page", 0), target_w=3000)
    tables, masks = cu.find_tables(img)
    t = next((x for x in tables if len(x["hlines"]) >= 9 and x["top"] < img.height * 0.5), None)
    if t is None:
        raise RuntimeError(f"main table not found {[(x['top'], len(x['hlines'])) for x in tables]}")
    head = cu.head_text(img, t, frac=0.05)
    ann["ocr_title"] = head[-80:]
    m = re.search(r"(\d{4})年(\d{1,2})月1日", head)
    if m and f"{int(m.group(1)):04d}-{int(m.group(2)):02d}" != ann["month"]:
        raise RuntimeError(f"执行时间 OCR says {m.group(0)}, expected {ann['month']}")
    tab = cu.read(img, t, masks, scale=1.0)
    n = len(tab["data"][0][0])
    while n > len(COLS) and all(nums[n - 1] is None for nums, _ in tab["data"]):
        n -= 1
    if n != len(COLS):
        raise RuntimeError(f"OCR: {n} numeric columns, expected {len(COLS)}")
    tab["data"] = [(nums[:n], raws[:n]) for nums, raws in tab["data"]]
    layout = LAYOUTS.get(len(tab["data"]))
    if layout is None:
        raise RuntimeError(f"OCR: {len(tab['data'])} data rows (known layouts: {sorted(LAYOUTS)})")
    rows, raws = cu.rows_from_table(tab, COLS, layout, ann["month"])
    fixes = cu.affine_solve(rows, raws, SHARED, ["sharp", "peak", "valley"], nd=6, tol=1.5e-6, log=log)
    cu.finish_rows(rows)
    cu.sanity(rows)
    ann["ocr_corrections"] = fixes
    ann["parse_method"] = f"OCR (tesseract) of 95598.cn image PDF page {ann.get('page', 0) + 1} + arithmetic cross-check ({len(fixes)} repairs)"
    ann["effective_from"], ann["effective_to"] = cms.effective_dates(head, ann["month"])
    return rows


def parse(ann, template=None, log=print):
    rows = cu.ocr_cache(CODE, ann, lambda: _ocr(ann, log), OCR_VERSION)
    for r, (tt, v) in zip(rows, LAYOUTS[len(rows)]):     # labels as printed (cache may hold older label text)
        r["tariff_type"], r["voltage"], r["voltage_rank"] = tt, v, pc.voltage_rank(v)
    return rows


def postprocess(parsed, anns):
    return cu.regulated_postprocess(parsed, anns, lambda m, rows: len(rows))


# ---------------------------------------------------------------- TOU
TOU_77 = ("https://fgw.hubei.gov.cn/fbjd/zc/gfwj/gf/202404/t20240401_5141591.shtml",
          "省发改委关于完善工商业分时电价机制有关事项的通知", "鄂发改价管〔2024〕77号", "2024-03-27", "2024-05-01", None)
TOU_86 = ("https://fgw.hubei.gov.cn/fbjd/zc/gfwj/gf/202504/t20250411_5612469.shtml",
          "省发改委关于实行节假日深谷电价机制有关事项的通知", "鄂发改价管〔2025〕86号", "2025-04-11", "2025-05-01", None)


def tou_rules():
    S = cms.segs_from_hours
    a = cms.tou_doc(CODE, *TOU_77)
    b = cms.tou_doc(CODE, *TOU_86)
    other = {"valley": [(0, 6), (12, 14)], "flat": [(6, 12), (14, 16)], "peak": [(16, 18), (20, 24)], "sharp": [(18, 20)]}
    summer = {"valley": [(0, 6), (12, 14)], "flat": [(6, 12), (14, 16)], "peak": [(16, 20), (22, 24)], "sharp": [(20, 22)]}
    ra = [([7, 8], "normal", S(summer)), ([1, 2, 3, 4, 5, 6, 9, 10, 11, 12], "normal", S(other))]
    # 春节、劳动节、国庆节 (dates per State Council): 9:00-15:00 深谷, other hours as on normal days of that month
    rb = [([1, 2, 5, 10], "holiday", S(dict(other, deep_valley=[(9, 15)])))]
    return [(a, cms.tou_rows(CODE, a, ra, TOU_ID_BASE)), (b, cms.tou_rows(CODE, b, rb, TOU_ID_BASE + 10))]


TOU_NOTES = [
    "TOU periods per 湖北省发改委《关于完善工商业分时电价机制有关事项的通知》鄂发改价管〔2024〕77号 (2024-03-27, effective "
    "2024-05-01; cited by the notices): 尖峰 7、8月 20-22, other months 18-20; 高峰 7、8月 16-20、22-24, other months 16-18、20-24; "
    "平 6-12、14-16; 谷 0-6、12-14 (尖峰 applies in every month).",
    "Holiday 深谷 per 鄂发改价管〔2025〕86号 (effective 2025-05-01): during 春节、劳动节、国庆节 (dates as announced by the State "
    "Council) 9:00-15:00 is 深谷 (float 0.9 for 9-12 & 14-15, 0.43 for 12-14); stored as a separate day_type='holiday' rule for "
    "months 1, 2, 5, 10.  The monthly notices print no 深谷 price, so deep_valley is NULL.",
    "Float: 尖峰:高峰:平:谷 = 1.8 : 1.49 : 1 : 0.48 in ordinary months, 尖峰 2 and 谷 0.45 in 7、8、12、1月, applied to 代理购电价格 + "
    "上网环节线损 (系统运行费、基金、输配电价 do not float), so the TOU spread is identical on every voltage row.",
    "Source: 95598.cn static image PDFs (网上国网 › 执行的电价和收费标准, region 湖北): combined 2025年1-12月 (pages 1-2 used) and "
    "2026年1-6月 notices, monthly from 2026-07. OCR'd with tesseract, cross-validated against the notice arithmetic; repairs logged.",
]
