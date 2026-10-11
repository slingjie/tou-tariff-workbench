"""国网福建省电力有限公司 代理购电工商业用户电价 — via an official GOVERNMENT repost (fallback).

fj.sgcc.com.cn is unreachable from the pipeline's network (connection reset / timeout, also from the Hark
browser) and no own-site list was identified, so the monthly table is taken from the 德化县人民政府 repost
"国网福建省电力有限公司代理购电工商业用户电价表（M月）" (政务公开 › 重点信息公开 › 公共企事业 › 供电):
  list    http://www.dehua.gov.cn/zwgk/zdxxgk/ggqsy/gd/   (titles/urls in an inline JS array, 15 per page)
  article http://www.dehua.gov.cn/zwgk/zdxxgk/ggqsy/gd/YYYYMM/tYYYYMMDD_NNNNNNN.htm  (HTML <table>)
The repost lags the official notice by ~1–2 weeks.  The HTML page is saved to data/raw/fujian/YYYY-MM.htm.
Formula row (published): 峰 = 2×(1+58%)+3+4+5+6, 谷 = 2×(1−63%)+…, 尖峰 = 2×(1+80%)+… (2 = 代理购电价格).
"""
import os, re
from urllib.parse import urljoin

from . import sgcc_cms as cms
from .. import parse_common as pc

REGION = {"code": "fujian", "name": "福建", "grid": "sgcc", "province": "福建省"}
LIST = "http://www.dehua.gov.cn/zwgk/zdxxgk/ggqsy/gd/"
RAW = os.path.join(pc.DATA, "raw", "fujian")
TITLE = re.compile(r"国网福建省电力有限公司代理购电工商业用户电价表[（(](\d{1,2})月[)）]")
_months = cms.months_between
TOU_ID_BASE = 5000


def discover(start, end, log=print):
    wanted = set(_months(start, end))
    html = pc.http_get(LIST).content.decode("utf-8", "ignore")
    items = re.findall(r"\"docreltime\":\s*'([^']+)',\s*\"doctitle\":\s*'([^']+)',\s*\"docpuburl\":\s*'([^']+)'", html)
    out = {}
    for date, title, url in items:
        m = TITLE.search(title)
        if not m:
            continue
        mon = int(m.group(1))
        y, pm = int(date[:4]), int(date[5:7])
        # repost is published in the month itself or up to ~6 weeks later; the year is the one whose month is closest
        year = y if mon <= pm + 1 else y - 1
        if pm == 12 and mon == 1:
            year = y + 1
        key = f"{year:04d}-{mon:02d}"
        if key in wanted and key not in out:
            out[key] = {"month": key, "title": title, "article_url": urljoin(LIST, url), "publish_date": date,
                        "file_url": urljoin(LIST, url), "file_ext": "htm", "discovered_via": "gov_repost:dehua.gov.cn"}
            log(f"  {key}: {out[key]['article_url']}")
    return [out[k] for k in sorted(out)]


def download(ann, log=print):
    os.makedirs(RAW, exist_ok=True)
    p = os.path.join(RAW, f"{ann['month']}.htm")
    if not (os.path.exists(p) and os.path.getsize(p) > 1000):
        r = pc.http_get(ann["file_url"])
        open(p, "wb").write(r.content)
        log(f"  downloaded {os.path.relpath(p, pc.ROOT)}")
    ann["file_path"] = p
    ann["sha256"] = pc.sha256_file(p)
    return p


def policy_doc_row(ann):
    d = cms.CMSRegion(REGION, LIST, "0", []).policy_doc_row(ann)
    d["title"] = ann["title"] + "（德化县人民政府转载）"
    return d


def parse(ann, template=None, log=print):
    html = open(ann["file_path"], encoding="utf-8", errors="ignore").read()
    tables = [g for g in cms.html_tables(html) if "电压等级" in cms.grid_text(g)]
    if not tables:
        raise RuntimeError("no tariff table in repost page")
    grid = tables[0]
    ann["effective_from"], ann["effective_to"] = cms.effective_dates(cms.grid_text(grid[:3]), ann["month"])
    if not ann["effective_from"].startswith(ann["month"]):
        raise RuntimeError(f"table is for {ann['effective_from']}, expected {ann['month']}")
    rows = cms.parse_grid(grid, ann["month"], category_fn=lambda lab, tt: "工商业用电",
                          voltage_fn=lambda v: v.replace("(", "（").replace(")", "）"))
    ann["parse_method"] = "html table (gov repost)"
    return rows


# ---------------------------------------------------------------- TOU 闽发改规〔2023〕8号
# Official text (福建省发改委 行政规范性文件库): 福建省发展和改革委员会关于完善分时电价政策的通知, 2023-11-28,
# "2024年1月1日起执行，有效期5年" → 2024-01-01 … 2028-12-31.  Still the document cited in note 2 of every monthly
# notice 2025-11..2026-10 (checked 2025-12, 2026-02/03/05/06 reposts); no newer 福建 工商业 TOU document found
# (2025/2026 docs are 中长期交易方案 闽发改规〔2025〕10号 etc., which keep the policy TOU as the settlement baseline).
# 峰 10-12, 15-20, 21-22; 谷 0-8; 其余平; 7-9月 尖峰 11-12, 17-18 (+80%); no 深谷 / holiday variant.
TOU_SOURCE = "https://fgw.fujian.gov.cn/zfxxgkzl/zfxxgkml/bwgfxwj/202311/t20231129_6309872.htm"
TOU_TITLE = "福建省发展和改革委员会关于完善分时电价政策的通知"


def tou_rules():
    doc = cms.tou_doc(REGION["code"], TOU_SOURCE, TOU_TITLE, "闽发改规〔2023〕8号", "2023-11-28", "2024-01-01",
                      "2028-12-31")
    base = {"peak": [(10, 12), (15, 20), (21, 22)], "valley": [(0, 8)]}
    summer = dict(base, sharp=[(11, 12), (17, 18)])
    rules = [([1, 2, 3, 4, 5, 6, 10, 11, 12], "normal", cms.segs_from_hours(base), "2024-01-01", "2028-12-31"),
             ([7, 8, 9], "normal", cms.segs_from_hours(summer), "2024-01-01", "2028-12-31")]
    return doc, cms.tou_rows(REGION["code"], doc, rules, TOU_ID_BASE)


TOU_NOTES = [
    "Source of record: 德化县人民政府 repost of the 国网福建 monthly table (fj.sgcc.com.cn unreachable from the pipeline).",
    "TOU per 闽发改规〔2023〕8号 (2023-11-28, eff. 2024-01-01, valid 5 years; official text on fgw.fujian.gov.cn; "
    "still cited by every notice in the window): 峰 10-12, 15-20, 21-22; 谷 0-8; others 平; "
    "7-9月 尖峰 11-12, 17-18. Float on 代理购电价格 only: 峰 +58%, 谷 −63%, 尖峰 +80%. No holiday/深谷 variant.",
    "深谷 not published (NULL).",
]


def _expected(r):
    P, f = r["purchase_price"], r["energy_price"]
    exp = {"flat": f, "peak": f + 0.58 * P, "valley": f - 0.63 * P}
    if r.get("sharp") is not None:
        exp["sharp"] = f + 0.80 * P
    return exp


def extra_validate(rows):
    return cms.tou_formula_check(rows, _expected, tol=0.0006)
