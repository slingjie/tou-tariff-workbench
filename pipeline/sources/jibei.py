"""国网冀北电力有限公司 代理购电工商业用户电价 — thin config over sgcc_cms (files from State Grid's 95598.cn copies).

Quirks
* Own list http://www.jibei.sgcc.com.cn/html/main/col40274/column_40274_1.html only carries rolling, overwritten
  articles (代理购电工商业用户电价表（当期）/（历史）) and the host is unreachable from the pipeline network and the Hark
  browser.  The monthly PDFs are published on State Grid's 网上国网 portal 95598.cn › 信息公开 › 执行的电价和收费标准
  (region 冀北); the browser collected the omg-static URLs into data/raw/jibei/seed.json (curl downloads them).
  (Gov mirror lflkq.lf.gov.cn is unreachable from the pipeline as well.)
* PDF page 1 = 附表1 代理购电工商业用户电价表 (loaded); 附表2 1.5倍, 附表3 价格表, 附表4 转供电 skipped.
  Columns 电度用电价格 | 代理购电价格 线损 输配 系统运行 基金 | 尖峰 高峰 平 低谷 (深谷 from 2026-01) | 需量 容量.
  尖峰 / 深谷 stored exactly as printed (the table prints them in some months the policy would not imply, e.g.
  2026-10 — kept as printed, never derived).  两部制 also has a 不满1千伏 row.
* TOU policy changed inside the range: 冀发改能价〔2023〕1711号 for 2025-11/12, 冀发改能价〔2025〕1275号 from 2026-01-01.
"""
from . import sgcc_cms as cms
from .. import parse_common as pc

REGION = {"code": "jibei", "name": "冀北", "grid": "sgcc", "province": "河北省（冀北电网：唐山 张家口 秦皇岛 承德 廊坊）"}
CMS = cms.CMSRegion(REGION, "http://www.jibei.sgcc.com.cn", "40274",
                    title_res=[r"(\d{4})年(\d{1,2})月账期冀北代理购电价格表"], max_pages=3,
                    timeout=15, live_list=False, file_pref=["pdf"])

_months = cms.months_between
discover = CMS.discover
download = CMS.download
policy_doc_row = CMS.policy_doc_row
TOU_ID_BASE = 10000


def parse(ann, template=None, log=print):
    import re
    pages = cms.pdf_tables(ann["file_path"])
    for i, text, tables in pages:
        head = re.sub(r"\s+", "", text[:200])
        if "代理购电工商业用户电价表" not in head or "1.5倍" in head or "转供电" in head:
            continue
        ann["effective_from"], ann["effective_to"] = cms.effective_dates(text, ann["month"])
        if not ann["effective_from"].startswith(ann["month"]):
            raise RuntimeError(f"附表1 is for {ann['effective_from']}, expected {ann['month']}")
        for g in tables:
            if "电压等级" in cms.grid_text(g):
                rows = cms.parse_grid(g, ann["month"], category_fn=lambda lab, tt: "工商业用电", nd=8)
                ann["parse_method"] = f"pdfplumber 附表1 p{i + 1}"
                return rows
    raise RuntimeError("附表1 代理购电工商业用户电价表 not found")


# ---------------------------------------------------------------- TOU
TOU_1711 = ("https://www.95598.cn/omg-static/99306111884397824210202129032439",
            "冀发改能价〔2023〕1711号 分时电价时段（按2025年11月代理购电工商业用户电价表注3转述）", "冀发改能价〔2023〕1711号",
            None, None, "2025-12-31")
TOU_1275 = ("https://www.hbkc.gov.cn/art/2025/12/4/art_5989_1095355.html",
            "河北省发展和改革委员会关于优化调整冀北电网工商业及其他用户分时电价政策的通知（宽城满族自治县人民政府转载）",
            "冀发改能价〔2025〕1275号", "2025-11-26", "2026-01-01")


def tou_rules():
    code = REGION["code"]
    d1, d2 = cms.tou_doc(code, *TOU_1711), cms.tou_doc(code, *TOU_1275)
    S = cms.segs_from_hours
    old = [([6, 7, 8], "normal", S({"sharp": [(17, 20)], "peak": [(10, 12), (16, 17), (20, 22)], "valley": [(0, 7), (23, 24)]}),
            None, "2025-12-31"),
           ([11, 12, 1], "normal", S({"sharp": [(17, 19)], "peak": [(8, 10), (16, 17), (19, 22)], "valley": [(1, 7), (12, 14)]}),
            None, "2025-12-31"),
           ([2, 3, 4, 5, 9, 10], "normal", S({"peak": [(8, 10), (16, 22)], "valley": [(1, 7), (12, 14)]}), None, "2025-12-31")]
    new = [([6, 7, 8], "normal", S({"sharp": [(18, 21)], "peak": [(10, 12), (16, 18), (21, 22)], "valley": [(0, 7), (23, 24)]}),
            "2026-01-01", None),
           ([1, 11, 12], "normal", S({"sharp": [(17, 19)], "peak": [(7, 9), (16, 17), (19, 22)], "valley": [(1, 6)],
                                      "deep_valley": [(12, 15)]}), "2026-01-01", None),
           ([2], "normal", S({"peak": [(16, 22)], "valley": [(1, 6)], "deep_valley": [(12, 15)]}), "2026-01-01", None),
           ([3, 4, 5, 9, 10], "normal", S({"peak": [(16, 22)], "valley": [(1, 6), (12, 15)]}), "2026-01-01", None)]
    return [(d1, cms.tou_rows(code, d1, old, TOU_ID_BASE)), (d2, cms.tou_rows(code, d2, new, TOU_ID_BASE + 50))]


TOU_NOTES = [
    "Files: State Grid 95598.cn static copies (网上国网 › 执行的电价和收费标准, region 冀北) of the monthly 代理购电价格表 PDF; "
    "jibei.sgcc.com.cn only keeps rolling 当期/历史 articles and is unreachable from the pipeline.",
    "TOU 2025-11..2025-12: 冀发改能价〔2023〕1711号 (夏6-8 尖峰17-20; 冬11,12,1 尖峰17-19, 谷1-7,12-14; 其他 峰8-10,16-22 谷1-7,12-14). "
    "From 2026-01-01: 冀发改能价〔2025〕1275号 (6-8 尖峰18-21; 1,11,12 尖峰17-19 深谷12-15 谷1-6; 2月 深谷12-15 谷1-6 峰16-22; "
    "3-5,9,10 谷1-6,12-15 峰16-22). <1kV users do not execute 尖峰/深谷 under 1275号.",
    "Float base = 当月平均购电价格 (平段): 峰/谷 ±70%, 尖峰 = 峰 +20%, 深谷 = 谷 −20%; 线损/输配/系统运行/历史偏差/基金 not floated. "
    "尖峰/深谷 stored exactly as printed in 附表1 (also when printed outside the policy months, e.g. 2026-10).",
]


def extra_validate(rows):
    """Per month the float base X (当月平均购电价格) is one number: X = (peak − flat)/0.7 from the first row; every other
    TOU cell must follow from it."""
    fails, X = [], {}
    for r in rows:
        if r.get("peak") is not None and r.get("flat") is not None:
            X.setdefault(r["month"], (r["peak"] - r["flat"]) / 0.7)

    def exp(r):
        x = X.get(r["month"])
        if x is None:
            return {}
        f = r["flat"]
        e = {"peak": f + 0.7 * x, "valley": f - 0.7 * x}
        if r.get("sharp") is not None:
            e["sharp"] = f + 0.7 * x + 0.2 * 1.7 * x
        if r.get("deep_valley") is not None:
            e["deep_valley"] = f - 0.7 * x - 0.2 * 0.3 * x
        return e
    fails += cms.tou_formula_check(rows, exp, tol=0.0006)
    fails += cms.tou_formula_check(rows, lambda r: {"flat": r["energy_price"]}, tol=0.0000005)
    return fails
