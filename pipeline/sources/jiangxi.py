"""国网江西省电力有限公司 代理购电工商业用户电价 — thin config over sgcc_cms, files from official copies (95598.cn / gov repost).

Quirks
* Own list: http://www.jx.sgcc.com.cn/html/main/col2786/column_2786_1.html ("国网江西省电力有限公司YYYY年M月代理购电价格公告").
  The host is unreachable from the pipeline network and from the Hark browser (empty reply, 2026-10-11), so
  discovery uses data/raw/jiangxi/seed.json: the State Grid static copies on 95598.cn (omg-static, text PDFs,
  located by browser search; 执行时间 verified) and, for 2026-04 / 2026-08, the same PDF reposted by 寻乌县人民政府
  (xunwu.gov.cn › 公共企事业单位信息 › 供电领域).  2025-11: 95598.cn omg-static text PDF found later, saved as
  data/raw/jiangxi/95598_2025-11.pdf (LOCAL_FILES).
* Table (page 1): 工商业用电 单一制 / 两部制, columns 电度用电价格 | 代理购电价格 线损 输配 系统运行 基金 | 尖峰 高峰 平
  低谷 深谷 | 需量 容量.  尖峰 and 深谷 are printed in the table (尖峰 only in 1, 7, 8, 12月) -> stored as printed.
  Layout: 3rd TDP period 单一制 3 rows (不满1 / 1-10 / 35千伏) + 两部制 4 rows; from 2026-08 (4th period) 5 + 4.
* The page also states 执行时间; parse() rejects a file whose 执行时间 is not the expected month (xunwu's "202603"
  article actually carries the 2025-09 table).
"""
import os
import re

from . import sgcc_cms as cms
from .. import parse_common as pc

REGION = {"code": "jiangxi", "name": "江西", "grid": "sgcc", "province": "江西省"}
CMS = cms.CMSRegion(REGION, "http://www.jx.sgcc.com.cn", "2786",
                    title_res=[r"国网江西省电力有限公司(\d{4})年(\d{1,2})月代理购电价格公告"], max_pages=4,
                    timeout=15, live_list=False, file_pref=["pdf"])

_months = cms.months_between
discover = CMS.discover
# months whose file was saved under a non-default local name (default f"{month}.pdf")
LOCAL_FILES = {"2025-11": "95598_2025-11.pdf"}


def download(ann, log=print):
    name = LOCAL_FILES.get(ann["month"])
    if name and ann.get("file_kind") != "img":
        p = CMS._fetch_to(ann["file_url"], os.path.join(CMS.raw_dir, name), CMS.MAGIC.get(ann["file_ext"]), log)
        ann["file_path"] = p
        ann["sha256"] = pc.sha256_file(p)
        return p
    return CMS.download(ann, log=log)
TOU_ID_BASE = 8000


def policy_doc_row(ann):
    return CMS.policy_doc_row(ann)


def parse(ann, template=None, log=print):
    pages = cms.pdf_tables(ann["file_path"])
    text = pages[0][1]
    ann["effective_from"], ann["effective_to"] = cms.effective_dates(text, ann["month"])
    if not ann["effective_from"].startswith(ann["month"]):
        raise RuntimeError(f"file is for {ann['effective_from']}, expected {ann['month']}")
    for i, t, tables in pages:
        if "1.5倍" in re.sub(r"\s+", "", t[:200]):
            continue
        for g in tables:
            if "电压等级" in cms.grid_text(g) and "代理购电" in cms.grid_text(g[:3]):
                rows = cms.parse_grid(g, ann["month"], category_fn=lambda lab, tt: "工商业用电", nd=8)
                ann["parse_method"] = f"pdfplumber table p{i + 1}"
                return rows
    raise RuntimeError("代理购电工商业用户电价表 not found")


# ---------------------------------------------------------------- TOU 赣发改价管〔2025〕463号
TOU_SRC = ("http://www.jx.sgcc.com.cn/html/main/col2786/2025-06/25/20250625152518539619489_1.html",
           "江西省发展改革委关于进一步完善分时电价机制有关事项的通知 — 时段/比例按每月代理购电工商业用户电价表注2转述",
           "赣发改价管〔2025〕463号", "2025-06-23", None)


def tou_rules():
    code = REGION["code"]
    doc = cms.tou_doc(code, *TOU_SRC)
    m112 = {"peak": [(9, 12), (18, 21)], "valley": [(0, 6)], "sharp": [(18, 20)]}
    m2 = {"peak": [(16, 22)], "valley": [(0, 6)]}
    m79 = {"peak": [(17, 23)], "valley": [(1, 5), (11.5, 14.5)], "deep_valley": [(12, 14)]}
    m78 = dict(m79, sharp=[(20.5, 22.5)])
    spring = {"peak": [(16, 22)], "valley": [(1, 5), (11.5, 14.5)], "deep_valley": [(12, 14)]}
    rules = [([1, 12], "normal", cms.segs_from_hours(m112)),
             ([2], "normal", cms.segs_from_hours(m2)),
             ([7, 8], "normal", cms.segs_from_hours(m78)),
             ([9], "normal", cms.segs_from_hours(m79)),
             ([3, 4, 5, 6, 10, 11], "normal", cms.segs_from_hours(spring)),
             # 春节 (Feb), 五一 (May), 国庆 (Oct): 12:00-14:00 深谷 on top of the month's schedule
             ([2], "holiday", cms.segs_from_hours(dict(m2, deep_valley=[(12, 14)]))),
             ([5, 10], "holiday", cms.segs_from_hours(spring))]
    return doc, cms.tou_rows(code, doc, rules, TOU_ID_BASE)


TOU_NOTES = [
    "jx.sgcc.com.cn unreachable (pipeline + browser): files are the State Grid 95598.cn static copies (and 寻乌县人民政府 reposts "
    "for 2026-04/08) of the official table (2025-11 from a 95598.cn static PDF found later).",
    "TOU per 赣发改价管〔2025〕463号 as restated in note 2 of every monthly table: 1、12月 峰 9-12, 18-21 (尖峰 18-20), 谷 0-6; "
    "2月 峰 16-22, 谷 0-6; 7-9月 峰 17-23 (7、8月 尖峰 20:30-22:30), 谷 1-5, 11:30-14:30 (深谷 12-14); 3-6、10-11月 峰 16-22, "
    "谷 1-5, 11:30-14:30 (深谷 12-14); 春节/五一/国庆 12-14 深谷. 463号's own effective date not verified (in force for the whole range).",
    "Float on 代理购电价格 only: 峰:平:谷 = 1.6:1:0.4, 尖峰 = 平 + 80%·P, 深谷 = 平 − 70%·P. 尖峰/深谷 stored only as printed in the table.",
]


def _expected(r):
    P, f = r["purchase_price"], r["energy_price"]
    exp = {"flat": f, "peak": f + 0.6 * P, "valley": f - 0.6 * P}
    if r.get("sharp") is not None:
        exp["sharp"] = f + 0.8 * P
    if r.get("deep_valley") is not None:
        exp["deep_valley"] = f - 0.7 * P
    return exp


def extra_validate(rows):
    return cms.tou_formula_check(rows, _expected, tol=0.0006)
