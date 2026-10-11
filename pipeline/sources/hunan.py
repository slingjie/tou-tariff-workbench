"""国网湖南省电力有限公司 代理购电工商业用户电价 (湖南) — 95598.cn PNG posters + OCR.

Quirks
* hn.sgcc.com.cn has no reachable list; the monthly notices "YYYY年M月代理购电电价" are single PNG posters
  (3023 px wide, rendered, no watermark) on 95598.cn › 信息公开 › 执行的电价和收费标准 (region 湖南); URLs in
  data/raw/hunan/seed.json.  Poster = 表1 代理购电工商业用户电价表 (loaded) + 1.5倍 / 价格信息 / 分时平均电价 tables (not loaded).
* 11 numeric columns: 平时段电度电价 (= 电度电价; stored as energy_price and flat), 上网电价 (代理购电价格, purchase_price),
  上网环节线损费用折价, 电度输配电价, 系统运行费用折价, 政府性基金及附加, 尖峰 / 高峰 / 低谷 时段电度电价, 最大需量, 变压器容量.
  Rows are printed from high to low voltage: 两部制 220千伏及以上 / 110 / 35 / 1~10千伏, then 单一制; to 2026-07 单一制 is
  110千伏及以上 / 35 / 1~10 / 不满1千伏 (8 rows), from 2026-08 (4th TDP period) 220千伏及以上 / 110 / 35 / 1~10 / 不满1 (9 rows).  尖峰 printed only in months with 尖峰 (others '-' → NULL); no 深谷.
* Float: 峰/谷 = 平 ± 60%·(上网电价 + 线损 + 输配电价) (checked on the posters) → TOU − energy affine in tdp; OCR
  cross-check = _central_util.affine_solve.
"""
import re

from . import _central_util as cu
from . import ocr_table as ot
from . import sgcc_cms as cms

REGION = {"code": "hunan", "name": "湖南", "grid": "sgcc", "province": "湖南省"}
CODE = REGION["code"]
OCR_VERSION = 1
TOU_ID_BASE = 24000
_months = cu.pc_months
COLS = ["energy_price", "purchase_price", "line_loss", "tdp", "system_op", "gov_fund", "sharp", "peak", "valley",
        "demand_price", "capacity_price"]
SHARED = ["purchase_price", "line_loss", "system_op", "gov_fund"]
LAYOUT = [("两部制", "220千伏及以上"), ("两部制", "110千伏"), ("两部制", "35千伏"), ("两部制", "1-10千伏"),
          ("单一制", "220千伏及以上"), ("单一制", "110千伏"), ("单一制", "35千伏"), ("单一制", "1-10千伏"),
          ("单一制", "不满1千伏")]
# 3rd TDP period (to 2026-07): 单一制 has one merged 110千伏及以上 row → 8 rows
LAYOUT_P3 = LAYOUT[:4] + [("单一制", "110千伏及以上"), ("单一制", "35千伏"), ("单一制", "1-10千伏"), ("单一制", "不满1千伏")]


def discover(start, end, log=print):
    return cu.seed_discover(CODE, start, end, log)


def download(ann, log=print):
    return cu.download(CODE, ann, log)


def policy_doc_row(ann):
    return cu.policy_doc_row(CODE, ann)


def _ocr(ann, log):
    img = cu.page_image(ann["file_path"])
    tables, masks = cu.find_tables(img)
    t = next((x for x in tables if len(x["hlines"]) >= 10 and x["top"] < img.height * 0.3), None)
    if t is None:
        raise RuntimeError(f"main table not found {[(x['top'], len(x['hlines'])) for x in tables]}")
    head = cu.head_text(img, t, frac=0.05)
    ann["ocr_title"] = head[-80:]
    m = re.search(r"(\d{4})年(\d{1,2})月1日", head)
    if m and f"{int(m.group(1)):04d}-{int(m.group(2)):02d}" != ann["month"]:
        raise RuntimeError(f"执行时间 OCR says {m.group(0)}, expected {ann['month']}")
    tab = cu.read(img, t, masks, scale=1.0)
    ncol = len(tab["data"][0][0])
    if ncol != len(COLS):
        raise RuntimeError(f"OCR: {ncol} numeric columns, expected {len(COLS)}")
    layout = LAYOUT if ann["month"] >= "2026-08" else LAYOUT_P3
    rows, raws = cu.rows_from_table(tab, COLS, layout, ann["month"])
    fixes = cu.affine_solve(rows, raws, SHARED, ["sharp", "peak", "valley"], nd=6, tol=1.5e-6, log=log,
                            flat_is_energy=False)
    for r in rows:
        r["flat"] = r["energy_price"]      # the notice's energy column IS the 平时段 price
    cu.finish_rows(rows)
    cu.sanity(rows)
    ann["ocr_corrections"] = fixes
    ann["parse_method"] = f"OCR (tesseract) of 95598.cn PNG poster + arithmetic cross-check ({len(fixes)} repairs)"
    ann["effective_from"], ann["effective_to"] = cms.effective_dates(head, ann["month"])
    return rows


def parse(ann, template=None, log=print):
    return cu.ocr_cache(CODE, ann, lambda: _ocr(ann, log), OCR_VERSION)


def postprocess(parsed, anns):
    return cu.regulated_postprocess(parsed, anns, lambda m, rows: "P4" if m >= "2026-08" else "P3",
                                    fields=("demand_price", "capacity_price"))


# ---------------------------------------------------------------- TOU (湘发改价调规〔2025〕385号)
TOU_385 = ("http://fgw.hunan.gov.cn/fgw/xxgk_70899/zcfg/dfxfg/202507/t20250702_33727950.html",
           "湖南省发展和改革委员会关于优化我省分时电价政策有关事项的通知", "湘发改价调规〔2025〕385号",
           "2025-06-24", "2025-08-01", "2030-07-31")


def tou_rules():
    d = cms.tou_doc(CODE, *TOU_385)
    base = {"valley": [(0, 6), (12, 14)], "flat": [(6, 12), (14, 16)], "peak": [(16, 24)]}
    S = cms.segs_from_hours
    rules = [([7, 8], "normal", S(dict(base, sharp=[(20, 24)]))),
             ([1, 12], "normal", S(dict(base, sharp=[(18, 22)]))),
             ([2, 3, 4, 5, 6, 9, 10, 11], "normal", S(base))]
    return d, cms.tou_rows(CODE, d, rules, TOU_ID_BASE)


TOU_NOTES = [
    "TOU periods per 湖南省发展改革委《关于优化我省分时电价政策有关事项的通知》湘发改价调规〔2025〕385号 (signed 2025-06-18, "
    "effective 2025-08-01 for 5 years; replaces 湘发改价调规〔2021〕848号): every month 谷 0-6、12-14, 平 6-12、14-16, 峰 16-24; "
    "尖峰 7、8月 20-24 and 1、12月 18-22. No 深谷 / holiday variant.",
    "Float: 峰:平:谷 = 1.6 : 1 : 0.4 applied to 上网电价 + 上网环节线损 + 输配电价 (系统运行费、基金、容需量电费 do not float); "
    "尖峰 = 峰 × 1.2.  The notice's 平时段电度电价 column is the 电度电价 (stored as energy_price and flat).  尖峰 is stored only where "
    "printed (尖峰 months); no 深谷.",
    "Source: 95598.cn static PNG posters (网上国网 › 执行的电价和收费标准, region 湖南); OCR'd with tesseract and cross-validated "
    "against the notice arithmetic; repairs logged in announcements.json ocr_corrections.",
]
