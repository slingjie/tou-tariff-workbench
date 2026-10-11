"""国网四川省电力公司 代理购电工商业用户电价 — thin config over sgcc_cms (files from State Grid's 95598.cn copies).

Quirks
* Own list: http://www.sc.sgcc.com.cn/html/main/col2804/column_2804_1.html — host returns an empty reply to the
  pipeline network and to the Hark browser (2026-10-11).  The same announcements are published on State Grid's
  网上国网 portal 95598.cn › 信息公开 › 执行的电价和收费标准 (region 四川); the browser collected the detail-page and
  omg-static file URLs into data/raw/sichuan/seed.json, and the static files download fine with curl.
* PDF: 表1 四川省省级代理购电工商业用户电价表 (page 1; loaded), 表2 1.5倍 (skipped), 表3 公开信息表 (skipped).
  Columns 非分时电度电价 | 代理购电价格 线损 输配 基金 系统运行 | 尖峰 高峰 平段 低谷 | 需量 容量; 尖峰 printed for 两部制
  rows (大工业 尖峰 policy) in the months where the table prints it -> stored as printed.  No 深谷 -> NULL.
  Layout: 3rd TDP period 单一制 3 rows (不满1 / 1-10 / 35千伏及以上) + 两部制 4; from 2026-08 单一制 5 rows.
* 2026-06: the 95598 row titled 6月 carries the July file; the June table was found as a separate 95598.cn omg-static
  text PDF (执行时间 2026-06-01..06-30), saved as data/raw/sichuan/95598_2026-06.pdf (seed entry, LOCAL_FILES).
* Missing: 2026-09 (only a .docx without a static URL).
"""
import os
import re

from . import sgcc_cms as cms
from .. import parse_common as pc

REGION = {"code": "sichuan", "name": "四川", "grid": "sgcc", "province": "四川省"}
CMS = cms.CMSRegion(REGION, "http://www.sc.sgcc.com.cn", "2804",
                    title_res=[r"国网四川省电力公司(\d{4})年(\d{1,2})月代理购电价格公告"], max_pages=4,
                    timeout=15, live_list=False, file_pref=["pdf"])

_months = cms.months_between
discover = CMS.discover
# months whose file was saved under a non-default local name (default f"{month}.pdf")
LOCAL_FILES = {"2026-06": "95598_2026-06.pdf"}


def download(ann, log=print):
    name = LOCAL_FILES.get(ann["month"])
    if name and ann.get("file_kind") != "img":
        p = CMS._fetch_to(ann["file_url"], os.path.join(CMS.raw_dir, name), CMS.MAGIC.get(ann["file_ext"]), log)
        ann["file_path"] = p
        ann["sha256"] = pc.sha256_file(p)
        return p
    return CMS.download(ann, log=log)
policy_doc_row = CMS.policy_doc_row
TOU_ID_BASE = 9000


def parse(ann, template=None, log=print):
    pages = cms.pdf_tables(ann["file_path"])
    for i, text, tables in pages:
        head = re.sub(r"\s+", "", text[:300])
        if "1.5倍" in head or "公开信息表" in head or "代理购电工商业用户电价表" not in head:
            continue
        ann["effective_from"], ann["effective_to"] = cms.effective_dates(text, ann["month"])
        if not ann["effective_from"].startswith(ann["month"]):
            raise RuntimeError(f"表1 is for {ann['effective_from']}, expected {ann['month']}")
        for g in tables:
            if "电压等级" in cms.grid_text(g):
                rows = cms.parse_grid(g, ann["month"], category_fn=lambda lab, tt: "工商业用电", nd=8)
                ann["parse_method"] = f"pdfplumber 表1 p{i + 1}"
                return rows
    raise RuntimeError("表1 四川省省级代理购电工商业用户电价表 not found")


# ---------------------------------------------------------------- TOU 川发改价格〔2025〕185号
TOU_185 = ("https://fgw.sc.gov.cn/sfgw/qtwj/2025/4/30/8f5b20c4de7e4df48faa04935e512111.shtml",
           "关于进一步调整我省分时电价机制的通知", "川发改价格〔2025〕185号", "2025-04-30", "2025-05-01")


def tou_rules():
    code = REGION["code"]
    doc = cms.tou_doc(code, *TOU_185)
    spring = {"peak": [(10, 12), (17, 22)], "valley": [(22, 8)]}
    summer = {"peak": [(11, 18), (20, 23)], "valley": [(1, 7)]}
    winter = {"peak": [(10, 12), (16, 22)], "valley": [(0, 8)]}
    rules = [([3, 4, 5, 6, 10, 11], "normal", cms.segs_from_hours(spring)),
             ([7, 8], "normal", cms.segs_from_hours(dict(summer, sharp=[(13, 14), (21, 23)]))),
             ([9], "normal", cms.segs_from_hours(summer)),
             ([12, 1, 2], "normal", cms.segs_from_hours(winter))]
    return doc, cms.tou_rows(code, doc, rules, TOU_ID_BASE)


TOU_NOTES = [
    "Files: State Grid 95598.cn static copies of the 国网四川 monthly 代理购电价格公告 (sc.sgcc.com.cn unreachable from pipeline and "
    "browser). 2026-06 from a separate 95598.cn static PDF (the 95598 row titled 6月 links the July file). 2026-09 missing "
    "(see data/raw/sichuan/seed.json 'missing').",
    "TOU per 川发改价格〔2025〕185号 (eff 2025-05-01), restated in note 3 of every notice; 川发改价格〔2025〕649号 (eff 2026-01-01) keeps "
    "it for 代理购电 users. 春秋(3-6,10,11月) 峰 10-12,17-22 谷 22-8; 夏(7-9月) 峰 11-18,20-23 谷 1-7; 冬(12,1,2月) 峰 10-12,16-22 谷 0-8; "
    "others 平. 峰/谷 = 平 ± 60% of (代理购电价格 + 线损 + 输配电价).",
    "尖峰 (大工业/两部制 分时用户): 7-8月 全月 13-14, 21-23; other months only when Chengdu forecasts ≥35℃ three days running "
    "(not modelled as a tou_rule). 尖峰 = 高峰 + 20%; stored only where the monthly table prints it. 深谷 not published (NULL).",
]


def _expected(r):
    B = r["purchase_price"] + r["line_loss"] + r["tdp"]
    f = r["energy_price"]
    exp = {"flat": f, "peak": f + 0.6 * B, "valley": f - 0.6 * B}
    if r.get("sharp") is not None:
        exp["sharp"] = f + 0.6 * B + 0.2 * 1.6 * B
    return exp


def extra_validate(rows):
    return cms.tou_formula_check(rows, _expected, tol=0.0006)
