"""贵州电网有限责任公司 代理购电工商业用户电价 — 南方电网 95598 API (official, text PDFs).

Source: 95598.csg.cn information API, areaCode 060000 (see _csg2_util), titles
"贵州电网有限责任公司YYYY年M月代理购电用户电价公告" (each filed under 3 columns, same PDF).  `link` = static page whose body
links the downloadFiles PDF.  PDF = 公告 text (pp.1-2) + 代理购电工商业用户电价公告表 (p.3, loaded) + 1.5倍 table (skipped)
+ 代理购电信息公开表.  Replaces the earlier county-repost/web_fetch capture (2025-11 / 2026-01 values were re-checked against
these PDFs: identical).

Table columns (header-mapped): 电量电价 | 代理购电价格 | 电量输配电价(不含线损) | 上网环节线损单价 | 系统运行费用单价 | 基金及附加 |
高峰 | 平 | 低谷 | 最大需量 | 变压器容量.  P/线损/系统运行/基金 are merged cells (filled down).  单一制 rows print no TOU
prices (481号: TOU applies to 两部制 users) → NULL.  3rd TDP period (to 2026-07): 单一制 不满1 / 1-10(20) / 35千伏,
两部制 1-10(20) / 35 / 110 / 220千伏及以上; 4th period (from 2026-08, 黔发改价格〔2026〕421号): 单一制 adds 110 / 220千伏及以上.
TOU float (note 2): 峰/谷 = 平 ± 60%·B, B = P + 输配 + 线损 + 系统运行费 (481号) to 2026-01, B = P + 线损 from 2026-02-01
(黔发改价格〔2026〕47号).
"""
import os, re

from . import _csg2_util as U
from . import sgcc_cms as cms
from .. import parse_common as pc

REGION = {"code": "guizhou", "name": "贵州", "grid": "csg", "province": "贵州省"}
AREA = "060000"
TITLE_RE = re.compile(r"贵州电网有限责任公司(\d{4})年(\d{1,2})月代理购电用户电价公告")
PREF_CLASS = ("a8d3146e14ed",)
_months = cms.months_between
TOU_ID_BASE = 63000


def discover(start, end, log=print):
    return U.discover(AREA, TITLE_RE, start, end, PREF_CLASS, log=log)


def download(ann, log=print):
    return U.download(REGION["code"], ann, log=log)


def policy_doc_row(ann):
    return U.policy_doc_row(REGION["code"], ann)


def _type(label):
    return "单一制" if "单一制" in label else ("两部制" if "两部制" in label else None)


def parse(ann, template=None, log=print):
    tbl, text = U.find_table(os.path.join(pc.ROOT, ann["file_path"]), "代理购电工商业用户电价公告表")
    em = U.exec_month(text)
    if em != ann["month"]:
        raise RuntimeError(f"执行时间 {em} != {ann['month']}")
    rows, cols = U.rows_from_table(tbl, ann["month"], _type)
    if len(rows) not in (7, 9):
        raise RuntimeError(f"{len(rows)} rows, expected 7 (3rd TDP period) or 9 (4th)")
    ann["parse_method"] = "pdfplumber table (header-mapped)"
    return rows


def _expected(r):
    if r.get("peak") is None:
        return {}
    B = r["purchase_price"] + r["line_loss"]
    if r["month"] < "2026-02":
        B += r["tdp"] + r["system_op"]
    f = r["energy_price"]
    return {"flat": f, "peak": f + 0.6 * B, "valley": f - 0.6 * B}


def extra_validate(rows):
    return U.tou_check(rows, _expected, tol=1.5e-6, label="gz_formula")


# ---------------------------------------------------------------- TOU
TOU_481 = ("https://95598.csg.cn/ucs/sr/info/06/a8d3146e14ed41d1ae18c8a7547c3cdc/20260730105307/60836cab0a254f06b07f214e1db42486.html",
           "省发展改革委关于完善峰谷分时电价机制有关事项的通知（南方电网95598发布，扫描件）", "黔发改价格〔2023〕481号",
           "2023-06-28", "2023-08-01")
SPEC_481 = {"peak": [(10, 13), (17, 22)], "valley": [(0, 8)]}
SPEC_FEB26 = {"peak": [(10, 11), (17, 22)], "valley": [(0, 8), (13, 15)]}


def tou_rules():
    code = REGION["code"]
    d = cms.tou_doc(code, *TOU_481)
    rules = [(list(range(1, 13)), "normal", cms.segs_from_hours(SPEC_481), "2023-08-01", "2026-01-31"),
             (list(range(1, 13)), "normal", cms.segs_from_hours(SPEC_481), "2026-03-01", None)]
    out = [(d, cms.tou_rows(code, d, rules, TOU_ID_BASE))]
    # 2026-02: periods printed in note 2 of the official 2026-02 notice (adjustment under 481号 五(六) 动态调整机制;
    # the adjusting document itself is not cited/published) – stored as its own rule for that month only.
    url = "https://95598.csg.cn/ucs/sr/info/06/a8d3146e14ed41d1ae18c8a7547c3cdc/20260130093930/498d44adaf634369b0cdf3bfab4e8fa1.html"
    feb = cms.tou_doc(code, url, "贵州电网有限责任公司2026年2月代理购电用户电价公告 注2 时段划分（当月调整）", None,
                      "2026-01-29", "2026-02-01", "2026-02-28")
    out.append((feb, cms.tou_rows(code, feb, [(list(range(1, 13)), "normal", cms.segs_from_hours(SPEC_FEB26),
                                               "2026-02-01", "2026-02-28")], TOU_ID_BASE + 10)))
    return out


TOU_NOTES = [
    "Source: 南方电网 95598 information API (areaCode 060000), monthly notice PDFs (text) 2025-11..2026-10.",
    "TOU per 黔发改价格〔2023〕481号 (eff 2023-08-01; official scan on 95598.csg.cn): 峰 10-13、17-22; 平 8-10、13-17、22-24; 谷 0-8; "
    "all months, no 尖峰/深谷; applies to 两部制 users (table prints TOU prices only for 两部制 rows). 峰/谷 = 平 ± 60%.",
    "Exception 2026-02: the official February 2026 notice (note 2) prints 峰 10-11、17-22; 平 8-10、11-13、15-17、22-24; 谷 0-8、13-15 "
    "(481号 allows dynamic adjustment; no separate adjusting document is cited) — stored as a rule for 2026-02 only, source = that notice.",
    "黔发改价格〔2026〕47号 (2026-01-22, official scan on 95598.csg.cn): from 2026-02-01 only 上网电价(代理购电价格) + 线损 float "
    "(输配、系统运行费、基金 no longer float); from 2026-03-01 两部制 users trading directly on the wholesale market (and retail "
    "pass-through volumes) no longer follow regulated TOU. Periods unchanged.",
]
