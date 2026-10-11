"""国网宁夏电力有限公司 代理购电工商业用户电价 — thin config over sgcc_cms (+ OCR, read-only use of ocr_table).

Quirks
* Column: 信息公开 › 供电企业执行的电价和收费标准 (col502367), article "代理购电价格表（YYYY年M月）" with one PDF.
  nx.sgcc.com.cn is unreachable from the pipeline network (curl timeout), so discovery uses
  data/raw/ningxia/seed.json (URLs collected once with the Hark browser, 2026-10-11) and the PDFs were saved by
  the browser into data/raw/ningxia/YYYY-MM.pdf (download() skips existing files).  Article pages print no
  发布日期: publish_date = date in the article URL path (= list date).
* The PDF is an image (2048 px scan, page 1 = 代理购电工商业用户电价表, page 2 = 价格信息表) -> OCR of page 1 with
  ocr_table.find_tables/read_table.  The table frame touches the image border, so x0 is clamped to 3.
* Market-based TOU: the table publishes 代理购电价格 per period (高峰/平/低谷 交易加权均价, cols 1-3) and the
  分时电度电价 (cols 8-10 = col1|2|3 + 线损 + 输配 + 基金 + 系统运行).  There is NO 非分时 (flat-rate) price:
  energy_price is stored = 平时段 total (col 9, formula 2+4+5+6+7) and purchase_price = 平时段 代理购电价格 (col 2);
  the 高峰/低谷 代理购电价格 are kept in announcements.json (period_purchase_prices).  No 尖峰/深谷 -> NULL.
* Layout: up to 2026-07 7 rows (单一制 不满1千伏 / 1-10千伏 / 35-110千伏; 两部制 1~10 / 35 / 110 / 220千伏及以上);
  from 2026-08 (4th TDP period) 9 rows (单一制 gains 35 / 110 / 220千伏及以上).  单一制 需量/容量 printed as 0 -> NULL.
"""
import os, re

from . import sgcc_cms as cms
from . import ocr_table as ot
from .. import parse_common as pc

REGION = {"code": "ningxia", "name": "宁夏", "grid": "sgcc", "province": "宁夏回族自治区"}
CMS = cms.CMSRegion(REGION, "http://www.nx.sgcc.com.cn", "502367",
                    title_res=[r"代理购电价格表[（(](\d{4})年(\d{1,2})月[)）]"], max_pages=3, timeout=15,
                    live_list=False)

_months = cms.months_between
discover = CMS.discover
download = CMS.download
policy_doc_row = CMS.policy_doc_row
TOU_ID_BASE = 7000
OCR_VERSION = 1

COLS = ["purchase_peak", "purchase_price", "purchase_valley", "line_loss", "tdp", "gov_fund", "system_op",
        "peak", "flat", "valley", "demand_price", "capacity_price"]
HEAD_KEYS = ["代理购电价格", "分时电度电价", "需量电价", "容量电价", "政府性基金", "系统运行", "高峰时", "低谷时", "配电价",
             "附加"]


def header_check(path):
    """OCR the header band once (chi_sim, block mode) and confirm the column headers the fixed column order relies
    on are there (multi-line headers interleave in OCR, so presence not order).  The column order itself is then
    proven per row by the table's formula row (8=1+4+5+6+7, 9=2+…, 10=3+…) in extra_validate.  Returns (hits, text)."""
    img = _page_image(path, 0)
    tables, a = ot.find_tables(img)
    t = max(tables, key=lambda t: t["bottom"] - t["top"])
    band = img.crop((0, t["hlines"][0], img.width, t["hlines"][3]))
    txt = re.sub(r"\s+", "", ot._tess(band, "chi_sim", psm=6))
    hits = sum(1 for k in HEAD_KEYS if k in txt)
    return hits, txt
LAYOUT = {
    7: [("单一制", v) for v in ("不满1千伏", "1-10千伏", "35-110千伏")] +
       [("两部制", v) for v in ("1~10千伏", "35千伏", "110千伏", "220千伏及以上")],
    9: [("单一制", v) for v in ("不满1千伏", "1-10千伏", "35千伏", "110千伏", "220千伏及以上")] +
       [("两部制", v) for v in ("1~10千伏", "35千伏", "110千伏", "220千伏及以上")],
}
# regulated 电度输配电价 per row (layout check): 3rd period (宁夏电网输配电价表 2023) / 4th period (2026-08 on)
TDP = {7: [0.1846, 0.1646, 0.1446, 0.0920, 0.0769, 0.0600, 0.0521],
       9: [0.1841, 0.1631, 0.1431, 0.1034, 0.0763, 0.0903, 0.0749, 0.0567, 0.0491]}


def _page_image(path, page=0):
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(path)
    imgs = [o for o in doc[page].get_objects() if o.type == 3]
    if not imgs:
        return doc[page].render(scale=3).to_pil().convert("L")
    best = max((o.get_bitmap(render=False).to_pil() for o in imgs), key=lambda b: b.width * b.height)
    return best.convert("L")


def _ocr(ann, log):
    img = _page_image(ann["file_path"], 0)
    tables, a = ot.find_tables(img)
    if not tables:
        raise RuntimeError("OCR: no ruled table on page 1")
    t = max(tables, key=lambda t: t["bottom"] - t["top"])
    t["x0"], t["x1"] = max(t["x0"], 3), min(t["x1"], img.width - 4)
    tab = ot.read_table(img, t, a, scale=1.5, log=log)
    if len(tab["cols"]) != 12:
        raise RuntimeError(f"OCR: expected 12 numeric columns, got {len(tab['cols'])}")
    n = len(tab["data"])
    if n not in LAYOUT:
        raise RuntimeError(f"OCR: unexpected number of data rows {n}")
    rows = []
    for (nums, raws), (tt, volt), tdp_reg in zip(tab["data"], LAYOUT[n], TDP[n]):
        rec = dict(zip(COLS, nums))
        if rec["tdp"] is None or abs(rec["tdp"] - tdp_reg) > 1e-9:
            raise RuntimeError(f"OCR: {tt} {volt} tdp={rec['tdp']} != regulated {tdp_reg} (layout mismatch)")
        if tt == "单一制":
            for f in ("demand_price", "capacity_price"):
                if rec[f] == 0:
                    rec[f] = None
        rows.append(rec | {"tariff_type": tt, "voltage": volt})
    return rows


def parse(ann, template=None, log=print):
    def run():
        rows = _ocr(ann, log)
        pp = {"peak": rows[0]["purchase_peak"], "flat": rows[0]["purchase_price"], "valley": rows[0]["purchase_valley"]}
        ann["period_purchase_prices"] = pp
        ann["effective_from"], ann["effective_to"] = cms.effective_dates("", ann["month"])
        ann["parse_method"] = "OCR (tesseract) of scanned PDF page 1, sha-keyed cache"
        out = []
        for r in rows:
            out.append({"month": ann["month"], "category": "工商业用电", "tariff_type": r["tariff_type"],
                        "voltage": r["voltage"], "voltage_rank": pc.voltage_rank(r["voltage"]),
                        "energy_price": r["flat"], "purchase_price": r["purchase_price"], "line_loss": r["line_loss"],
                        "tdp": r["tdp"], "system_op": r["system_op"], "gov_fund": r["gov_fund"], "sharp": None,
                        "peak": r["peak"], "flat": r["flat"], "valley": r["valley"], "deep_valley": None,
                        "demand_price": r["demand_price"], "capacity_price": r["capacity_price"],
                        "_p_peak": r["purchase_peak"], "_p_valley": r["purchase_valley"]})
        return out
    rows = ot.cached_parse(ann, REGION["code"], run, version=OCR_VERSION)
    # keep cache keys (period_purchase_prices is not in cached_parse's keep-list): rebuild from rows
    if rows and "_p_peak" in rows[0]:
        ann["period_purchase_prices"] = {"peak": rows[0]["_p_peak"], "flat": rows[0]["purchase_price"],
                                         "valley": rows[0]["_p_valley"]}
    _PERIOD[ann["month"]] = ann.get("period_purchase_prices")
    hits, txt = header_check(ann["file_path"])
    ann["header_check"] = f"{hits}/{len(HEAD_KEYS)} header keywords found"
    if hits < 7:
        raise RuntimeError(f"header check failed ({hits}/{len(HEAD_KEYS)}): {txt[:120]}")
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]


_PERIOD = {}


# ---------------------------------------------------------------- TOU (periods restated in note 6 of each notice)
TOU_2025 = ("http://www.nx.sgcc.com.cn/html/files/2025-10/28/20251028174546362481154.pdf",
            "自治区发展改革委关于做好2025年电力中长期交易有关事项的通知 — 峰平谷时段按代理购电价格表注6转述",
            "宁发改运行〔2024〕952号", None, "2025-01-01", "2026-03-31")
TOU_2026 = ("https://fzggw.nx.gov.cn/tzgg/202602/t20260212_5170559.html",
            "自治区发展改革委关于印发《宁夏回族自治区电网企业代理购电工作实施细则》的通知 — 2026年4月起代理购电峰平谷时段"
            "（经自治区发改委同意，按代理购电价格表注6）", "宁发改价格（管理）〔2026〕79号", "2026-02-12", "2026-04-01", None)


def tou_rules():
    code = REGION["code"]
    d1 = cms.tou_doc(code, *TOU_2025)
    d2 = cms.tou_doc(code, *TOU_2026)
    allm = list(range(1, 13))
    r1 = [(allm, "normal", cms.segs_from_hours({"peak": [(7, 9), (17, 23)], "valley": [(9, 17)]}),
           "2025-01-01", "2026-03-31")]
    r2 = [(allm, "normal", cms.segs_from_hours({"peak": [(21, 5)], "valley": [(9, 17)]}), "2026-04-01", None)]
    return [(d1, cms.tou_rows(code, d1, r1, TOU_ID_BASE)), (d2, cms.tou_rows(code, d2, r2, TOU_ID_BASE + 50))]


TOU_NOTES = [
    "Source of record: nx.sgcc.com.cn col502367 monthly 代理购电价格表 PDFs (scanned images, OCR'd; every value cross-checked "
    "by the table's own formula row and the regulated 输配电价). PDFs saved via the Hark browser because the host is unreachable "
    "from the pipeline network.",
    "Market-based TOU: 代理购电价格 differs per period (峰/平/谷 交易加权均价). No 非分时 price is published: energy_price = 平时段 "
    "total (formula 2+4+5+6+7) and purchase_price = 平时段 代理购电价格; 高峰/低谷 代理购电价格 are in data/raw/ningxia/announcements.json.",
    "2025-11..2026-03: 峰 7-9, 17-23; 谷 9-17; 平 0-7, 23-24 (2025 periods, 宁发改运行〔2024〕952号). In 2026-01..03 the 峰 price "
    "was BELOW 平 (market outcome) — tou_order validation failures for those months are real, not OCR errors.",
    "From 2026-04: 峰 21:00-次日5:00; 谷 9-17; 平 5-9, 17-21 (per 实施细则 宁发改价格（管理）〔2026〕79号, approved by 自治区发改委).",
    "尖峰/深谷 not published (NULL). 单一制 需量/容量 printed as 0 -> NULL. Layout: 7 rows until 2026-07, 9 rows from 2026-08 "
    "(4th TDP period).",
]


def _expected(r):
    pp = _PERIOD.get(r["month"]) or {}
    rest = r["line_loss"] + r["tdp"] + r["gov_fund"] + r["system_op"]
    exp = {"flat": r["purchase_price"] + rest}
    if pp.get("peak") is not None:
        exp["peak"] = pp["peak"] + rest
    if pp.get("valley") is not None:
        exp["valley"] = pp["valley"] + rest
    return exp


def extra_validate(rows):
    return cms.tou_formula_check(rows, _expected, tol=0.00006)
