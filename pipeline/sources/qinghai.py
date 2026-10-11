"""国网青海省电力公司 代理购电工商业用户电价.

Quirks
* qh.sgcc.com.cn and fgw.qinghai.gov.cn are unreachable from the pipeline network; notices are the 95598.cn omg-static
  copies (网上国网 › 执行的电价和收费标准, region 青海), data/raw/qinghai/seed.json (browser-collected 2026-10-11).
  2025-11..2026-07 were (re)published on 95598 on 2026-07-10 (listing publish date); the PDFs carry their original
  signing date on page 1 (e.g. 2025-10-28).
* Text PDF: p1 公告, p2 附件1 代理购电工商业用户电价表 (+ notes), [p3 notes], last 附件2 代理购电价格信息表.
  pdfplumber table → sgcc_cms.parse_grid; the 代理购电价格/线损/系统运行费/基金 merged cell is filled across rows.
* Layout: to 2026-07 单一制 不满1 / 1-10 / 35千伏 + 两部制 1-10 / 35 / 110-220 / 330千伏 = 7 rows; from 2026-08 (4th TDP
  period, 青发改价格〔2026〕405号) 单一制 adds 110 / 220千伏及以上 and 两部制's top row becomes 220千伏及以上 → 9 rows.
* No 尖峰 / 深谷 columns.  Category 工商业用电.
"""
import re

from . import sgcc_cms as cms
from . import _northwest_util as nw
from .. import parse_common as pc

REGION = {"code": "qinghai", "name": "青海", "grid": "sgcc", "province": "青海省"}
TOU_ID_BASE = 74000

_months = cms.months_between


def discover(start, end, log=print):
    return nw.seed_discover(REGION["code"], start, end, log)


def download(ann, log=print):
    return nw.download(REGION["code"], ann, log)


def policy_doc_row(ann):
    return nw.policy_doc_row(REGION["code"], ann)


def parse(ann, template=None, log=print):
    i, text, grid = nw.find_page_table(ann["file_path"], [r"代理购电工商业用户电价表"])
    nw.check_month(text, ann["month"])
    rows = cms.parse_grid(grid, ann["month"], category_fn=lambda lab, t: "工商业用电")
    nw.fill_shared(rows)
    nw.zero_to_none(rows)
    for r in rows:
        if r["tariff_type"] is None:
            raise RuntimeError(f"no 单一制/两部制 label for {r['voltage']}")
    ann["effective_from"], ann["effective_to"] = cms.effective_dates(text, ann["month"])
    ann["parse_method"] = f"pdfplumber text table, page {i + 1}"
    return rows


# ---------------------------------------------------------------- TOU
# The periods are restated in note 2 of every monthly notice 2025-11..2026-10 (official grid document); they are the
# periods of 青海省发改委《关于优化完善我省峰谷分时电价政策的通知》 (2024-04-09, effective 2024-05-01, which repealed
# 青发改价格〔2021〕814号 / 〔2023〕647号 / 〔2023〕775号).  The DRC page (fgw.qinghai.gov.cn) is unreachable and its 文号
# was not captured, so the policy_doc points at the official notice that restates it.
TOU_SRC = ("https://www.95598.cn/omg-static/99309241946254462759200214872640",
           "青海省发展和改革委员会关于优化完善我省峰谷分时电价政策的通知（时段及浮动比例按国网青海省电力公司2026年10月代理购电价格公告附件1注2所载）",
           None, "2024-04-09", "2024-05-01")


def tou_rules():
    code = REGION["code"]
    doc = nw.tou_doc(code, *TOU_SRC, id_salt="#tou")
    spec = {"peak": [(7, 9), (17, 23)], "valley": [(9, 17)], "flat": [(0, 7), (23, 24)]}
    rules = [(list(range(1, 13)), "normal", cms.segs_from_hours(spec))]
    return doc, cms.tou_rows(code, doc, rules, TOU_ID_BASE)


TOU_NOTES = [
    "TOU periods as printed in note 2 of every monthly notice 2025-11..2026-10: 高峰 7:00-9:00、17:00-23:00; 低谷 9:00-17:00; "
    "其余平段 (all 执行峰谷分时 工商业 users, all months).  Float base = 代理购电价格 P: 高峰 = 平 + 63%·P, 低谷 = 平 − 65%·P.",
    "Policy: 青海省发改委《关于优化完善我省峰谷分时电价政策的通知》(2024-04-09, eff 2024-05-01; 文号 not captured – DRC site unreachable). "
    "Its text (as reposted) also sets 尖峰 in 一、四季度 8:00-9:00 and 19:00-21:00 at 平 + 100%; the grid's monthly notices in the "
    "window neither mention 尖峰 nor print a 尖峰 price, so no 尖峰 rule/price is stored (unverified against the official DRC page).",
    "From 2026-08 the 4th TDP period (青发改价格〔2026〕405号) applies: more 单一制 voltage rows, new 两部制 220千伏及以上 row.",
    "Source: 95598.cn omg-static copies (网上国网 › 执行的电价和收费标准, region 青海).",
]


def _expected(r):
    P = r["purchase_price"]
    flat = r["energy_price"]
    return {"flat": flat, "peak": flat + 0.63 * P, "valley": flat - 0.65 * P}


def extra_validate(rows):
    return cms.tou_formula_check(rows, _expected, tol=0.0000015)
