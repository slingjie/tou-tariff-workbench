"""海南电网有限责任公司 代理购电工商业用户电价 — 南方电网 95598 API (official, text PDFs).

Source: 95598.csg.cn information API, areaCode 070000 (see _csg2_util), titles
"海南电网有限责任公司代理购电工商业用户电价表（YYYY年M月）"; `link` is the downloadFiles PDF itself (3 columns, same file).
PDF p.1 = 代理购电工商业用户电价表 (loaded, notes 1-4 incl. TOU), p.2 = 代理购电价格表.

Columns (header-mapped): 电度用电价格 | 代理购电价格 | 电度输配电价 | 系统运行费用折价 | 上网环节线损电价 | 政府性基金及附加 |
[尖峰 (5、6、7月)] | 高峰 | 平 | 低谷 | 需量电价 | 容量电价.  Sub-classes go in `category`:
工商业用电(100千伏安以下) = 单一制 (含行政事业单位办公场所用电), 工商业用电(100千伏安及以上) = 两部制.
3rd TDP period (to 2026-07): 单一制 不满1 / 1-10千伏, 两部制 35千伏以下 / 35 / 110 / 220千伏及以上 (需量 35.2 / 容量 22 flat);
4th period (from 2026-08, 发改价格〔2026〕1077号): 单一制 不满1 / 1-10 / 35 / 110 / 220千伏及以上, 两部制 1-10 / 35 / 110 / 220.
'35千伏以下' (两部制, P3) gets voltage_rank 2.
TOU float (note 2, 琼发改规〔2021〕18号): 峰 = 平 + 70%·B, 谷 = 平 − 60%·B, 尖峰 = 峰 + 20%·(1.7·B);
B = 代理购电价格 + 输配电价 to 2026-07, B = 代理购电价格 only from 2026-08 (checked on every row).
"""
import os, re

from . import _csg2_util as U
from . import sgcc_cms as cms
from .. import parse_common as pc

REGION = {"code": "hainan", "name": "海南", "grid": "csg", "province": "海南省"}
AREA = "070000"
TITLE_RE = re.compile(r"海南电网有限责任公司代理购电工商业用户电价表（(\d{4})年(\d{1,2})月）")
PREF_CLASS = ("00ab16ad011b",)
_months = cms.months_between
TOU_ID_BASE = 64000


def discover(start, end, log=print):
    return U.discover(AREA, TITLE_RE, start, end, PREF_CLASS, log=log)


def download(ann, log=print):
    return U.download(REGION["code"], ann, log=log)


def policy_doc_row(ann):
    return U.policy_doc_row(REGION["code"], ann)


def _type(label):
    if "单一制" in label or "100千伏安以下" in label:
        return "单一制"
    if "两部制" in label or "100千伏安及以上" in label:
        return "两部制"
    return None


def _cat(label):
    if "100千伏安以下" in label:
        return "工商业用电(100千伏安以下)"
    if "100千伏安及以上" in label:
        return "工商业用电(100千伏安及以上)"
    raise RuntimeError(f"unknown class label {label}")


def parse(ann, template=None, log=print):
    tbl, text = U.find_table(os.path.join(pc.ROOT, ann["file_path"]), "代理购电工商业用户电价表")
    em = U.exec_month(text)
    if em != ann["month"]:
        raise RuntimeError(f"执行时间 {em} != {ann['month']}")
    rows, cols = U.rows_from_table(tbl, ann["month"], _type, _cat)
    for r in rows:
        if r["voltage"].startswith("35千伏以下"):
            r["voltage_rank"] = 2
    n1 = sum(r["tariff_type"] == "单一制" for r in rows)
    n2 = len(rows) - n1
    if (n1, n2) not in ((2, 4), (5, 4)):
        raise RuntimeError(f"unexpected layout 单一制 {n1} / 两部制 {n2}")
    ann["parse_method"] = "pdfplumber table (header-mapped)"
    return rows


def _expected(r):
    B = r["purchase_price"] + (r["tdp"] if r["month"] < "2026-08" else 0)
    f = r["energy_price"]
    peak = f + 0.7 * B
    return {"flat": f, "peak": peak, "valley": f - 0.6 * B, "sharp": peak + 0.2 * 1.7 * B}


def extra_validate(rows):
    return U.tou_check(rows, _expected, tol=1.5e-6, label="hn_formula")


# ---------------------------------------------------------------- TOU
TOU_18 = ("https://95598.csg.cn/ucs/sr/info/hn/07-firm/211223112311/52b72e0f866e4826a0508bee193c8298.html",
          "海南省发展和改革委员会关于进一步完善峰谷分时电价机制有关问题的通知（南方电网95598发布：正文图片+附件1）",
          "琼发改规〔2021〕18号", "2021-12-21", "2022-02-01")
SPEC = {"peak": [(10, 12), (16, 22)], "flat": [(7, 10), (12, 16), (22, 23)], "valley": [(23, 7)]}


def tou_rules():
    code = REGION["code"]
    d = cms.tou_doc(code, *TOU_18)
    sharp = dict(SPEC, sharp=[(20, 22)])
    rules = [([5, 6, 7], "normal", cms.segs_from_hours(sharp)),
             ([1, 2, 3, 4, 8, 9, 10, 11, 12], "normal", cms.segs_from_hours(SPEC))]
    return d, cms.tou_rows(code, d, rules, TOU_ID_BASE)


TOU_NOTES = [
    "Source: 南方电网 95598 information API (areaCode 070000), monthly table PDFs (text) 2025-11..2026-10.",
    "TOU per 琼发改规〔2021〕18号 (2021-12-21, eff 2022-02-01; still cited by every notice): 峰 10-12、16-22; 平 7-10、12-16、22-23; "
    "谷 23-次日7; 尖峰 20-22 in 5、6、7月 (+20% on the peak price). 峰 +70%, 谷 −60%; float base 代理购电价格+输配电价 to 2026-07, "
    "代理购电价格 only from 2026-08 (notice note 2). 尖峰 prices are printed in May–July tables only. No 深谷.",
    "EV charging facilities have separate seasonal periods (琼发改规〔2024〕2号, note 3) — not stored (not general 工商业 tariff).",
]
