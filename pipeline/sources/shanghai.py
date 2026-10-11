"""国网上海市电力公司 代理购电工商业用户电价 — thin config over sgcc_cms.

Quirks
* List/article HTML on sh.sgcc.com.cn is behind a Ruishu ($_ts) JS anti-bot wall: curl gets a 2.4 KB
  challenge page.  Discovery therefore falls back to data/raw/shanghai/seed.json (URLs collected once in a
  real browser).  The /html/files/*.xls attachments are static and download fine with curl.
* Attachment is an .xls workbook with 3 sheets: 表2 代理购电价格信息表, 表1 代理购电工商业用户电价表, 表3 执行1.5倍…
  Only 表1 is loaded (1.5倍 table skipped by convention).
* Layout change 2026-08 (4th TDP period, 发改价格〔2026〕1077号): before = 一般工商业用电 (单一制 3 rows + 两部制 5 rows)
  + 大工业用电 两部制 (5 rows) = 13 rows; from 2026-08 = 工商业用电 单一制（一般工商业用电） 5 rows + 两部制（大工业及
  一般工商业用电） 5 rows = 10 rows.  Category = the table's own 用电分类 label.
* 深谷 is published (大工业 / 2026-08+ all 两部制); 尖峰 published in 1, 7, 8, 12月 for 两部制.
"""
from . import sgcc_cms as cms
from .. import parse_common as pc

REGION = {"code": "shanghai", "name": "上海", "grid": "sgcc", "province": "上海市"}
CMS = cms.CMSRegion(REGION, "http://www.sh.sgcc.com.cn", "2017082164",
                    title_res=[r"上海市(\d{4})年(\d{1,2})月代理购电(?:公示表|价格表)"], max_pages=3)

_months = cms.months_between
discover = CMS.discover
download = CMS.download
policy_doc_row = CMS.policy_doc_row
TOU_ID_BASE = 2000


def _category(labels, tariff):
    lab = labels[0] if labels else ""
    if "大工业用电" == lab or lab.startswith("大工业"):
        return "大工业用电"
    if lab.startswith("一般工商业"):
        return "一般工商业用电"
    return "工商业用电"


def parse(ann, template=None, log=print):
    sheets = cms.xls_sheets(ann["file_path"])
    pick = [(n, g) for n, g in sheets if "工商业用户电价表" in n and "1.5" not in n]
    if not pick:  # fall back on the sheet title row
        pick = [(n, g) for n, g in sheets if "代理购电工商业用户电价表" in cms.grid_text(g[:2]) and "1.5" not in cms.grid_text(g[:2])]
    if not pick:
        raise RuntimeError(f"表1 sheet not found in {[n for n, _ in sheets]}")
    name, grid = pick[0]
    ann["effective_from"], ann["effective_to"] = cms.effective_dates(cms.grid_text(grid[:3]), ann["month"])
    rows = cms.parse_grid(grid, ann["month"], category_fn=_category)
    ann["parse_method"] = f"xlrd sheet '{name}'"
    return rows


# ---------------------------------------------------------------- TOU (沪发改价管〔2022〕50号 + 〔2024〕28号)
TOU_50 = ("https://fgw.sh.gov.cn/fgw_jggl/20221216/e2652e3ab7ee49438d6e82af8880b160.html",
          "关于进一步完善我市分时电价机制有关事项的通知", "沪发改价管〔2022〕50号", "2022-12-16", "2023-01-01")
TOU_28 = ("https://fgw.sh.gov.cn/fgw_jggl/20240613/c753f8153e15492898c2ea3737ee93d5.html",
          "关于增加大工业深谷电价实施时间有关事项的通知", "沪发改价管〔2024〕28号", "2024-06-13", "2024-09-01")


def _two_part(month):
    """两部制分时用户 periods (50号): summer 7-9 vs other months; 尖峰 7-8 12-14, 1/12 19-21."""
    if month in (7, 8, 9):
        spec = {"peak": [(8, 15), (18, 21)], "flat": [(6, 8), (15, 18), (21, 22)], "valley": [(22, 6)]}
        if month in (7, 8):
            spec["sharp"] = [(12, 14)]
    else:
        spec = {"peak": [(8, 11), (18, 21)], "flat": [(6, 8), (11, 18), (21, 22)], "valley": [(22, 6)]}
        if month in (1, 12):
            spec["sharp"] = [(19, 21)]
    return spec


def _with_deep(spec):
    s = dict(spec)
    s["deep_valley"] = [(0, 6), (22, 24)]
    return s


def tou_rules():
    """Returns [(doc, rules), …]: 50号 normal-day periods; 28号 深谷 on holidays / 休息日."""
    code = REGION["code"]
    d50 = cms.tou_doc(code, *TOU_50)
    d28 = cms.tou_doc(code, *TOU_28)
    normal = [(months, "normal", cms.segs_from_hours(_two_part(rep)))
              for months, rep in [([7, 8], 7), ([9], 9), ([1, 12], 1), ([2, 3, 4, 5, 6, 10, 11], 2)]]
    # 元旦(1) 春节(1/2) 清明(4) 劳动(5) 端午(5/6) 中秋(9/10) 国庆(10); 休息日 in 2-6, 9-11
    deep = [([1], "holiday", cms.segs_from_hours(_with_deep(_two_part(1)))),
            ([2, 4, 5, 6, 10], "holiday", cms.segs_from_hours(_with_deep(_two_part(2)))),
            ([9], "holiday", cms.segs_from_hours(_with_deep(_two_part(9)))),
            ([2, 3, 4, 5, 6, 10, 11], "weekend", cms.segs_from_hours(_with_deep(_two_part(2)))),
            ([9], "weekend", cms.segs_from_hours(_with_deep(_two_part(9))))]
    return [(d50, cms.tou_rows(code, d50, normal, TOU_ID_BASE)),
            (d28, cms.tou_rows(code, d28, deep, TOU_ID_BASE + 50))]


TOU_NOTES = [
    "TOU periods per 沪发改价管〔2022〕50号 as restated in every monthly xls note 2: tou_rules are the 两部制 (大工业及一般工商业) "
    "schedule. 一般工商业单一制分时用户 use a 2-period schedule instead: 高峰 06:00-22:00, 低谷 22:00-06:00 (no 平/尖峰) — not stored as a tou_rule.",
    "尖峰 (sharp): 7-8月 12:00-14:00 and 1、12月 19:00-21:00, 两部制 only; sharp = peak + 25% (on the floated base).",
    "深谷 (deep_valley) per 沪发改价管〔2024〕28号: 大工业 (from 2026-08 the merged 两部制 class) on 元旦/春节/清明/劳动/端午/中秋/国庆 "
    "and on 休息日 (weekend rules) in 2-6月 and 9-11月, 00:00-06:00 and 22:00-24:00; price = 平段 base −80%. Holiday dates per the national notice.",
    "Float base B = 代理购电价格 + 电度输配电价. 两部制: 夏(7-9)/冬(1,12) 峰 +80% 谷 −60%, other months 峰 +60% 谷 −50%; "
    "单一制: 夏/冬 峰 +20% 谷 −45%, other months 峰 +17% 谷 −45%.",
    "Category follows each month's table: up to 2026-07 一般工商业用电 (单一制/两部制) + 大工业用电 (两部制); from 2026-08 (4th TDP period) "
    "工商业用电 单一制/两部制 only. 1.5倍 table (表3) not loaded.",
]


def _expected(r):
    m = int(r["month"][5:])
    B = r["purchase_price"] + r["tdp"]
    flat = r["energy_price"]
    hot = m in (7, 8, 9, 1, 12)
    if r["tariff_type"] == "两部制":
        up, dn = (0.8, 0.6) if hot else (0.6, 0.5)
    else:
        up, dn = (0.2, 0.45) if hot else (0.17, 0.45)
    exp = {"flat": flat, "peak": flat + up * B, "valley": flat - dn * B, "deep_valley": flat - 0.8 * B}
    exp["sharp"] = flat + up * B + 0.25 * (1 + up) * B
    return exp


def extra_validate(rows):
    return cms.tou_formula_check(rows, _expected, tol=0.0006)
