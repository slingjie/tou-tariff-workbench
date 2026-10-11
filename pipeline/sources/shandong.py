"""国网山东省电力公司 代理购电工商业用户电价 (山东) — 95598.cn posters + OCR.

Quirks
* No own list reachable (sd.sgcc.com.cn unreachable); the 12 monthly notices "国网山东省电力公司YYYY年M月代理购电价格"
  come from 95598.cn › 信息公开 › 执行的电价和收费标准 (region 山东), URLs in data/raw/shandong/seed.json.
  2025-11..2026-07 are JPG posters (2481×5359), 2026-08/09 image-only PDFs, 2026-10 a text PDF – all are OCR'd the
  same way (PDFs rendered at ~3150 px width) for consistency.
* Poster = 代理购电工商业用户电价表 (loaded) + 执行1.5倍 table (skipped) + 电价信息公布表 (not loaded).
  18 numeric columns: 1 电度用电价格 = 2 当月平均购电价格 + 3 容量补偿电价 + 4 历史偏差电费折价 (= 代理工商业购电价格,
  stored as purchase_price) + 5 上网环节线损费用折价 + 6 抽水蓄能容量电费 + 7 煤电容量电费 + 8 新能源可持续发展价格结算
  机制差价结算费用 + 9 各类分摊损益 (6..9 = 系统运行费用, stored as system_op) + 10 政府性基金及附加 + 11 输配电价;
  then 尖峰 / 高峰 / 平 / 低谷 / 深谷 (all printed every month, for every row) and 需量 / 容量.
* Float (printed formulas): 尖峰 = 2·200%+3·200%+4+5·200%+6·200%+7·200%+8·200%+9+10+11 etc. → TOU − energy is the
  same constant on every row; OCR cross-check = _central_util.affine_solve (Σ components, 平 = 电度, constant TOU spread).
* Layout: to 2026-07 (3rd TDP period) 7 rows – 单一制 不满1千伏 / 1-10(20)千伏 / 35千伏及以上, 两部制 1-10(20)千伏 /
  35-110千伏以下 / 110-220千伏以下 / 220千伏及以上; from 2026-08 (4th period, 鲁发改价格〔2026〕556号) 9 rows –
  单一制 不满1 / 1-10(20) / 35 / 110 / 220千伏及以上, 两部制 1-10(20) / 35 / 110 / 220千伏及以上.
"""
import re

from . import _central_util as cu
from . import ocr_table as ot
from . import sgcc_cms as cms
from .. import parse_common as pc

REGION = {"code": "shandong", "name": "山东", "grid": "sgcc", "province": "山东省"}
CODE = REGION["code"]
OCR_VERSION = 1
P4_FROM = "2026-08"
TOU_ID_BASE = 21000

_months = cu.pc_months
COLS18 = ["energy_price", "_p_avg", "_p_cap", "_p_hist", "line_loss", "_s_pump", "_s_coal", "_s_new", "_s_alloc",
          "gov_fund", "tdp", "sharp", "peak", "flat", "valley", "deep_valley", "demand_price", "capacity_price"]
SHARED = ["_p_avg", "_p_cap", "_p_hist", "line_loss", "_s_pump", "_s_coal", "_s_new", "_s_alloc", "gov_fund"]
LAYOUT_P3 = [("单一制", "不满1千伏"), ("单一制", "1-10(20)千伏"), ("单一制", "35千伏及以上"),
             ("两部制", "1-10(20)千伏"), ("两部制", "35-110千伏以下"), ("两部制", "110-220千伏以下"),
             ("两部制", "220千伏及以上")]
LAYOUT_P4 = [("单一制", "不满1千伏"), ("单一制", "1-10(20)千伏"), ("单一制", "35千伏"), ("单一制", "110千伏"),
             ("单一制", "220千伏及以上"), ("两部制", "1-10(20)千伏"), ("两部制", "35千伏"), ("两部制", "110千伏"),
             ("两部制", "220千伏及以上")]


def discover(start, end, log=print):
    return cu.seed_discover(CODE, start, end, log)


def download(ann, log=print):
    return cu.download(CODE, ann, log)


def policy_doc_row(ann):
    return cu.policy_doc_row(CODE, ann)


def _ocr(ann, log):
    img = cu.page_image(ann["file_path"], target_w=3150)
    tables, masks = cu.find_tables(img)
    t = next((x for x in tables if len(x["hlines"]) >= 10), None)
    if t is None:
        raise RuntimeError(f"main table not found {[(x['top'], len(x['hlines'])) for x in tables]}")
    head = cu.head_text(img, t, frac=0.09)
    ann["ocr_title"] = head[-80:]
    m = re.search(r"执行时间[：:]?(\d{4})年(\d{1,2})月", head)
    if m and f"{int(m.group(1)):04d}-{int(m.group(2)):02d}" != ann["month"]:
        raise RuntimeError(f"执行时间 OCR says {m.group(0)}, expected {ann['month']}")
    tab = cu.read(img, t, masks, scale=1.5)
    ncol = len(tab["data"][0][0])
    while ncol > 18 and all(n[ncol - 1] is None for n, _ in tab["data"]):
        ncol -= 1
    if ncol == 17:      # months before the 新能源可持续发展价格结算机制 column was added
        cols = [c for c in COLS18 if c != "_s_new"]
    elif ncol == 18:
        cols = COLS18
    else:
        raise RuntimeError(f"OCR: {ncol} numeric columns, expected 17 or 18")
    layout = LAYOUT_P4 if ann["month"] >= P4_FROM else LAYOUT_P3
    rows, raws = cu.rows_from_table(tab, cols, layout, ann["month"])
    shared = [f for f in SHARED if f in cols]
    fixes = cu.affine_solve(rows, raws, shared, ["sharp", "peak", "valley", "deep_valley"], nd=8, tol=1.5e-6, log=log)
    for r in rows:
        r["purchase_price"] = round(r.pop("_p_avg") + r.pop("_p_cap") + r.pop("_p_hist"), 6)
        r["system_op"] = round(r.pop("_s_pump") + r.pop("_s_coal") + r.pop("_s_new", 0.0) + r.pop("_s_alloc"), 6)
    cu.finish_rows(rows)
    cu.sanity(rows)
    ann["ocr_corrections"] = fixes
    ann["parse_method"] = f"OCR (tesseract) of 95598.cn {ann['file_ext'].upper()} + arithmetic cross-check ({len(fixes)} repairs)"
    ann["effective_from"], ann["effective_to"] = cms.effective_dates(head, ann["month"])
    return rows


def parse(ann, template=None, log=print):
    return cu.ocr_cache(CODE, ann, lambda: _ocr(ann, log), OCR_VERSION)


def _period(m, rows=None):
    return "P4" if m >= P4_FROM else "P3"


def postprocess(parsed, anns):
    return cu.regulated_postprocess(parsed, anns, _period)


# ---------------------------------------------------------------- TOU
TOU_2026 = ("https://www.95598.cn/omg-static/99106241082992543400401950838465",
            "国网山东省电力公司关于2026年工商业分时电价公告（2026年沿用2025年分时时段不变）",
            "鲁发改价格〔2023〕914号", "2025-11-28", "2025-01-01", "2026-09-30")
TOU_2610 = ("https://www.95598.cn/omg-static/99109111502938089123900953785807",
            "2026年10月1日起山东将执行新的分时电价峰谷时段（国网山东省电力公司）",
            "鲁发改价格函〔2026〕68号", "2026-09-11", "2026-10-01", None)


def tou_rules():
    A = cms.tou_doc(CODE, *TOU_2026)
    B = cms.tou_doc(CODE, *TOU_2610)
    S = cms.segs_from_hours
    winter = {"valley": [(2, 6), (10, 15)], "deep_valley": [(11, 14)], "peak": [(7, 9), (16, 21)], "sharp": [(16, 19)]}
    summer = {"valley": [(1, 6)], "peak": [(16, 23)], "sharp": [(17, 22)]}
    ra = [([1, 2, 12], "normal", S(winter)),
          ([3, 4, 5], "normal", S({"valley": [(10, 15)], "deep_valley": [(11, 14)], "peak": [(17, 22)], "sharp": [(17, 20)]})),
          ([6], "normal", S({"valley": [(7, 12)], "peak": [(16, 23)], "sharp": [(17, 22)]})),
          ([7, 8], "normal", S(summer)),
          ([9, 10, 11], "normal", S({"valley": [(10, 15)], "deep_valley": [(11, 14)], "peak": [(16, 21)], "sharp": [(17, 19)]}))]
    rb = [([1, 2, 12], "normal", S(winter)),
          ([3, 4, 5, 6], "normal", S({"valley": [(8, 15)], "deep_valley": [(10, 14)], "peak": [(17, 23)], "sharp": [(17, 22)]})),
          ([7, 8], "normal", S(summer)),
          ([9, 10, 11], "normal", S({"valley": [(9, 15)], "deep_valley": [(11, 14)], "peak": [(16, 22)], "sharp": [(17, 20)]}))]
    ra = [r + (A["effective_from"], A["effective_to"]) for r in ra]
    return [(A, cms.tou_rows(CODE, A, ra, TOU_ID_BASE)), (B, cms.tou_rows(CODE, B, rb, TOU_ID_BASE + 10))]


TOU_NOTES = [
    "TOU periods: 国网山东省电力公司《关于2026年工商业分时电价公告》(2025-11-28, under 鲁发改价格〔2023〕914号; states that 2026 "
    "keeps the 2025 periods unchanged, so it covers 2025-11..2026-09): 1-2、12月 谷2-6、10-15 (深谷11-14) 峰7-9、16-21 (尖峰16-19); "
    "3-5月 谷10-15 (深谷11-14) 峰17-22 (尖峰17-20); 6月 谷7-12 峰16-23 (尖峰17-22); 7-8月 谷1-6 峰16-23 (尖峰17-22); 9-11月 谷10-15 "
    "(深谷11-14) 峰16-21 (尖峰17-19). From 2026-10-01 (鲁发改价格函〔2026〕68号, 国网山东 notice 2026-09-11, restated in the 2026-10 "
    "price notice): 3-6月 谷8-15 (深谷10-14) 峰17-23 (尖峰17-22); 9-11月 谷9-15 (深谷11-14) 峰16-22 (尖峰17-20); 1-2、12月 and 7-8月 "
    "unchanged. No holiday variant is published in these notices.",
    "Float: 峰 +70%, 谷 −70%, 尖峰 +100%, 深谷 −90%, applied to 当月平均购电价格, 容量补偿电价, 线损, 抽蓄容量电费, 煤电容量电费 and "
    "新能源差价结算费用 (历史偏差、各类分摊损益、基金、输配电价 do not float), so the TOU spread is identical on every voltage row. "
    "尖峰 and 深谷 are printed for every month and row and stored as printed.",
    "purchase_price = 代理工商业购电价格 (当月平均购电价格 + 容量补偿电价 + 历史偏差电费折价); system_op = 抽蓄容量电费 + 煤电容量电费 + "
    "新能源可持续发展价格结算机制差价结算费用 + 各类分摊损益折合度电水平 (the notice prints these four under 系统运行费用折合度电水平).",
    "Source: 95598.cn static copies (网上国网 › 执行的电价和收费标准, region 山东); OCR'd with tesseract and cross-validated against "
    "the notice arithmetic; repairs logged in announcements.json ocr_corrections. 1.5倍 table and 信息公布表 not loaded.",
]
