"""国网陕西省电力有限公司 代理购电工商业用户电价 — 陕西（不含榆林地区）.

Quirks
* sn.sgcc.com.cn is unreachable; notices are the 95598.cn omg-static copies (网上国网 › 执行的电价和收费标准, region
  陕西), listed in data/raw/shaanxi/seed.json (browser-collected 2026-10-11).  One text PDF per month:
  p1 代理购电工商业用户电价表（不含榆林地区）, [p2 1.5倍 (不含榆林)], p 榆林地区, [1.5倍 榆林], last page 代理购电价格表.
  The 1.5倍 pages exist from 2026-05 and are skipped.  The 榆林 table is loaded by region ``shaanxi-yulin``
  (same file, different 两部制 TDP) — see shaanxi_yulin.py.
* Text PDF → pdfplumber table → sgcc_cms.parse_grid (columns by header text).  代理购电价格 / 线损 / 系统运行费 /
  基金 are one merged cell; pdfplumber places the value in one row of the span (2026-10: the 220千伏 row), so they
  are filled across the table (``fill_shared``).
* 尖峰 column prints 0 in months without 尖峰 → NULL; printed 尖峰 in 1、7、8、12月 is stored.  No 深谷.
* Layout: to 2026-07 一般工商业用电(单一制) 不满1 / 1-10(20) / 35 / 110千伏 + 大工业用电(两部制) 1-10(20) / 35 / 110 /
  220(330)千伏 = 8 rows; from 2026-08 (4th TDP period) 单一制 gains 220千伏及以上 → 9 rows.
* Category = the table's 用电分类 label without the (单一制)/(两部制) suffix: 一般工商业用电 / 大工业用电.
"""
import re

from . import sgcc_cms as cms
from . import _northwest_util as nw
from .. import parse_common as pc

REGION = {"code": "shaanxi", "name": "陕西(不含榆林)", "grid": "sgcc", "province": "陕西省"}
TOU_ID_BASE = 71000
PAGE_WANT = [r"代理购电工商业用户电价表", r"不含榆林"]
ID_SALT = ""

_months = cms.months_between


def discover(start, end, log=print):
    return nw.seed_discover(REGION["code"], start, end, log)


def download(ann, log=print):
    return nw.download(REGION["code"], ann, log)


def policy_doc_row(ann):
    return nw.policy_doc_row(REGION["code"], ann, ID_SALT)


def _category(labels, tariff):
    lab = labels[0] if labels else ""
    lab = re.sub(r"\((单一制|两部制)\)", "", lab)
    return lab or "工商业用电"


def parse_page(ann, want, log=print):
    i, text, grid = nw.find_page_table(ann["file_path"], want)
    nw.check_month(text, ann["month"])
    rows = cms.parse_grid(grid, ann["month"], category_fn=_category)
    nw.fill_shared(rows)
    nw.zero_to_none(rows)
    for r in rows:
        if r["tariff_type"] is None:
            raise RuntimeError(f"no 单一制/两部制 label for {r['voltage']}")
    ann["effective_from"], ann["effective_to"] = cms.effective_dates("", ann["month"])
    ann["parse_method"] = f"pdfplumber text table, page {i + 1}"
    return rows


def parse(ann, template=None, log=print):
    return parse_page(ann, PAGE_WANT, log)


# ---------------------------------------------------------------- TOU: 陕发改价格〔2025〕1034号 (eff 2025-08-01)
TOU_1034 = ("https://sndrc.shaanxi.gov.cn/zfxxgk/zc/fgwj/sfzggwwj/2025/202507/t20250723_3546317.html",
            "陕西省发展和改革委员会关于调整陕西电网分时电价政策有关事项的通知", "陕发改价格〔2025〕1034号",
            "2025-07-23", "2025-08-01")


def _spec(sharp=None):
    s = {"valley": [(0, 6), (11, 14)], "flat": [(6, 11), (14, 16), (23, 24)], "peak": [(16, 23)]}
    if sharp:
        s["sharp"] = [sharp]
    return s


def tou_rules():
    code = REGION["code"]
    doc = nw.tou_doc(code, *TOU_1034, id_salt=ID_SALT)
    rules = [([1, 12], "normal", cms.segs_from_hours(_spec((18, 20)))),
             ([7, 8], "normal", cms.segs_from_hours(_spec((19, 21)))),
             ([2, 3, 4, 5, 6, 9, 10, 11], "normal", cms.segs_from_hours(_spec()))]
    return doc, cms.tou_rows(code, doc, rules, TOU_ID_BASE)


TOU_NOTES = [
    "TOU periods per 陕发改价格〔2025〕1034号 (2025-07-21, effective 2025-08-01; replaces 陕发改价格〔2021〕1757号), restated "
    "unchanged in note 4 of every monthly notice 2025-11..2026-10: 高峰 16:00-23:00; 低谷 0:00-6:00、11:00-14:00; 平 "
    "6:00-11:00、14:00-16:00、23:00-24:00; 尖峰 7、8月 19:00-21:00, 1、12月 18:00-20:00 (代理购电工商业用户, 大工业 and 一般工商业).",
    "Float base = 代理工商业购电价格 P only (输配/线损/系统运行费/基金 do not float): 高峰 = 平 + 70%·P, 低谷 = 平 − 70%·P, "
    "尖峰 = 平 + 90%·P.  The table prints 0 in the 尖峰 column in months without 尖峰 → stored NULL.  No 深谷.",
    "Region split: 国网陕西 publishes two tables in the same PDF — 不含榆林地区 (this region) and 榆林地区 (region shaanxi-yulin); "
    "they differ in the 大工业(两部制) 电度输配电价.  1.5倍 tables (from 2026-05) not loaded.",
    "Source: 95598.cn omg-static copies (网上国网 › 执行的电价和收费标准, region 陕西); provincial sgcc host unreachable.",
]


def _expected(r):
    P = r["purchase_price"]
    flat = r["energy_price"]
    exp = {"flat": flat, "peak": flat + 0.7 * P, "valley": flat - 0.7 * P}
    if r.get("sharp") is not None:
        exp["sharp"] = flat + 0.9 * P
    return exp


def extra_validate(rows):
    return cms.tou_formula_check(rows, _expected, tol=0.0000015)
