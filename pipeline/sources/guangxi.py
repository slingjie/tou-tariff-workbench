"""广西电网有限责任公司 代理购电工商业用户电价 — 南方电网 95598 API (official, text PDFs).

Source: 95598.csg.cn information API, areaCode 040000 (see _csg2_util), titles "YYYY年M月代理购电相关价格表" (filed under 4
columns; the attachment is byte-identical in all of them).  `link` = static page with 3 JPG previews + the downloadFiles PDF.
PDF p.1 = 广西电网有限责任公司代理购电工商业用户电价表 (loaded), p.2 = 执行1.5倍… (skipped), p.3 = 代理购电信息表.

Columns (header-mapped): 电量电价 | 代理购电价格 | 上网环节线损费用 | 输配电价 | 系统运行费用 | [其他 (3rd period only)] |
政府性基金及附加 | 高峰 | 平 | 低谷 | 最大需量 | 变压器容量.  The '其他' column printed to 2026-07 (信息表 '其他折价' = 峰谷分时电价
损益 shared by all 工商业 users, e.g. 0.008410 in 2025-11, all rows) has no field in the schema → not stored; for those months
the generic components_sum check reports the difference (= 其他) when |其他| > 0.0005 (2025-11, 2025-12, 2026-02).
From 2026-08 the item sits inside 系统运行费用 (信息表 item 15 '居民农业电价交叉补贴新增损益等').
3rd TDP period: 单一制 不满1 / 1~10 / 35千伏, 两部制 1~10 / 35 / 110 / 220千伏; 4th (from 2026-08, 发改价格〔2026〕1077号):
单一制 + 110 / 220千伏及以上.  No 尖峰/深谷 printed.
TOU prices: 峰/谷 = 平 ± D with one D per month; D = 50% × (当月)平均上网电价 (信息表 item 5) — checked every month.
"""
import os, re

import pdfplumber

from . import _csg2_util as U
from . import sgcc_cms as cms
from .. import parse_common as pc

REGION = {"code": "guangxi", "name": "广西", "grid": "csg", "province": "广西壮族自治区"}
AREA = "040000"
TITLE_RE = re.compile(r"^(\d{4})年(\d{1,2})月代理购电相关价格表$")
PREF_CLASS = ("4ead135257dc",)
_months = cms.months_between


def discover(start, end, log=print):
    return U.discover(AREA, TITLE_RE, start, end, PREF_CLASS, log=log)


def download(ann, log=print):
    return U.download(REGION["code"], ann, log=log)


def policy_doc_row(ann):
    return U.policy_doc_row(REGION["code"], ann)


def _type(label):
    return "单一制" if "单一制" in label else ("两部制" if "两部制" in label else None)


AVG_RE = re.compile(r"(?:当月)?平均上网电价\s*\d+\s*(-?\d+\.\d+)")


def parse(ann, template=None, log=print):
    path = os.path.join(pc.ROOT, ann["file_path"])
    tbl, text = U.find_table(path, "代理购电工商业用户电价表")
    em = U.exec_month(text)
    if em != ann["month"]:
        raise RuntimeError(f"执行时间 {em} != {ann['month']}")
    rows, cols = U.rows_from_table(tbl, ann["month"], _type)
    if len(rows) not in (7, 9):
        raise RuntimeError(f"{len(rows)} rows, expected 7 or 9")
    # '其他' column (not stored) – keep it in the announcement record for transparency
    hdr = {c: "".join(re.sub(r"\s+", "", r[c] or "") for r in tbl[:3]) for c in range(len(tbl[0]))}
    other_c = [c for c, h in hdr.items() if h == "其他"]
    if other_c:
        vals = sorted({U._cellnum(r[other_c[0]]) for r in tbl if U._cellnum(r[other_c[0]]) is not None})
        ann["other_component_not_stored"] = vals
    with pdfplumber.open(path) as pdf:
        info = "\n".join(pg.extract_text() or "" for pg in pdf.pages if "代理购电信息表" in (pg.extract_text() or "")[:200])
    m = AVG_RE.search(info)
    avg = float(m.group(1)) if m else None
    for r in rows:
        r["_avg_onGrid"] = avg
    ann["avg_on_grid_price"] = avg
    ann["parse_method"] = "pdfplumber table (header-mapped)"
    return rows


def extra_validate(rows):
    fails = []
    for r in rows:
        avg = r.get("_avg_onGrid")
        if avg is None or r.get("peak") is None:
            continue
        D = round(0.5 * avg, 6)
        for k, v in (("flat", r["energy_price"]), ("peak", r["energy_price"] + D), ("valley", r["energy_price"] - D)):
            if abs(r[k] - v) > 1.5e-6:
                fails.append({"month": r["month"], "category": r["category"], "tariff_type": r["tariff_type"],
                              "voltage": r["voltage"], "check": f"gx_formula_{k}",
                              "detail": f"{k}={r[k]} expected={round(v, 6)} (D=50%×当月平均上网电价 {avg})"})
    return fails


TOU_NOTES = [
    "Source: 南方电网 95598 information API (areaCode 040000), monthly PDFs (text) 2025-11..2026-10.",
    "NO tou_rule stored: every notice only says '峰谷分时电价按自治区发展改革委现行有关规定执行'. The last verifiable official text is "
    "桂发改价格〔2023〕609号 (峰 11-13、17-23; 平 7-11、13-17; 谷 23-7), but a Nov-2024 consultation draft (峰 16-24; 平 0-1、7-12、14-16; "
    "谷 1-7、12-14; 35 kV+ at once, others from 2025-03-01) was apparently finalised and its official text could not be retrieved "
    "(fgw.gxzf.gov.cn unreachable / not archived), so periods in force during 2025-11..2026-10 are unverified.",
    "Printed TOU prices: 峰/谷 = 平 ± 50% × 当月平均上网电价 (信息表 item 5); 输配、线损、系统运行费、基金不浮动.",
    "3rd-period tables (to 2026-07) print an extra component '其他' (其他折价 = 峰谷分时电价损益分摊) that the schema has no field "
    "for; it is not stored, so Σ components ≠ 电量电价 by that amount (components_sum failures for 2025-11 +0.00841, 2025-12 +0.00561, "
    "2026-02 +0.001501; smaller values in other months are within tolerance). See announcements.json other_component_not_stored.",
]
