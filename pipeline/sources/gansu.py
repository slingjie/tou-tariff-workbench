"""国网甘肃省电力公司 代理购电工商业用户电价.

Quirks
* gs.sgcc.com.cn has no usable list; notices are the 95598.cn omg-static copies (网上国网 › 执行的电价和收费标准,
  region 甘肃), data/raw/gansu/seed.json (browser-collected 2026-10-11).  The 甘肃 95598 listing has NO 代理购电
  announcement for 2025-11 and 2025-12 (searched "代理购电", "11月", "12月" and paged the full 4-page listing): the
  series there starts with 2026-01 → those two months are missing.
* Formats: 2026-01/02/03/08/09/10 one PNG poster (2245 px wide, digital render: 电价表 + 价格信息表); 2026-04/05/07 PDF
  whose page 1 is one embedded image of the 电价表; 2026-06 a text PDF.  Images → OCR (ocr_table primitives, own grid
  reader below); 2026-06 → pdfplumber.  The column order is fixed by the table's 公式 row and proven per row by
  the table's own arithmetic (``_check``), so OCR is accepted only when every row satisfies it exactly.
* Market-based TOU (like 宁夏): the table publishes 代理购电价格 (上网电价) and 上网环节线损电价 per 高峰 / 平 / 低谷
  period (cols 1-6), then 电度输配电价 (7), 系统运行费折价 (8), 基金 (9), 分时电度电价 高峰/平/低谷 (10=1+4+7+8+9,
  11=2+5+7+8+9, 12=3+6+7+8+9), 需量 (13), 容量 (14).  There is NO 非分时 price: energy_price = 平时段 total (col 11),
  purchase_price / line_loss = the 平时段 values (cols 2 / 5); the 高峰/低谷 purchase and line-loss prices are kept in
  announcements.json (period_prices).  No 尖峰 / 深谷.
* Layout: 3rd TDP period (to 2026-07) 7 rows – 单一制 不满1 / 1~10（20） / 35千伏, 两部制 1~10（20） / 35 / 110 / 220千伏及以上;
  4th period (from 2026-08, 发改价格〔2026〕1077号) 9 rows – 单一制 adds 110 / 220千伏及以上.  Rows are identified by the
  regulated 电度输配电价 (must match the period's TDP list row for row).
"""
import os

import numpy as np

from . import sgcc_cms as cms
from . import ocr_table as ot
from . import _northwest_util as nw
from .. import parse_common as pc

REGION = {"code": "gansu", "name": "甘肃", "grid": "sgcc", "province": "甘肃省"}
TOU_ID_BASE = 73000
OCR_VERSION = 1

_months = cms.months_between

COLS = ["p_peak", "p_flat", "p_valley", "ll_peak", "ll_flat", "ll_valley", "tdp", "system_op", "gov_fund",
        "peak", "flat", "valley", "demand_price", "capacity_price"]
V1, V10, V35, V110, V220 = "不满1千伏", "1~10（20）千伏", "35千伏", "110千伏", "220千伏及以上"
LAYOUT = {7: [("单一制", V1), ("单一制", V10), ("单一制", V35),
              ("两部制", V10), ("两部制", V35), ("两部制", V110), ("两部制", V220)],
          9: [("单一制", V1), ("单一制", V10), ("单一制", V35), ("单一制", V110), ("单一制", V220),
              ("两部制", V10), ("两部制", V35), ("两部制", V110), ("两部制", V220)]}
TDP = {7: [0.2965, 0.2765, 0.2565, 0.1028, 0.0888, 0.0764, 0.0658],                       # 发改价格〔2023〕526号
       9: [0.2945, 0.2745, 0.2545, 0.1251, 0.1134, 0.1063, 0.0923, 0.0799, 0.0693]}      # 发改价格〔2026〕1077号


def discover(start, end, log=print):
    return nw.seed_discover(REGION["code"], start, end, log)


def download(ann, log=print):
    return nw.download(REGION["code"], ann, log)


def policy_doc_row(ann):
    return nw.policy_doc_row(REGION["code"], ann)


# ---------------------------------------------------------------- readers
def _image(path):
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    if path.endswith(".pdf"):
        import pypdfium2 as pdfium
        page = pdfium.PdfDocument(path)[0]
        imgs = [o for o in page.get_objects() if o.type == 3]
        if not imgs:
            return page.render(scale=3).to_pil().convert("L")
        return max((o.get_bitmap(render=False).to_pil() for o in imgs), key=lambda b: b.width * b.height).convert("L")
    im = Image.open(path)
    if im.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        im = im.convert("RGBA")
        bg.paste(im, mask=im.split()[-1])
        im = bg
    return im.convert("L")


def ocr_grid(img, scale=1.5, n_label=3, n_head_lines=4, log=print):
    """Ruled-table reader for posters whose shared columns are ONE merged cell over all data rows.
    Row rules are taken from a TOU-price column (ruled on every row); a numeric column without inner row rules
    is read once over the whole data band.  Returns (rows of floats, rows of raw strings, merged col indexes)."""
    tables, a = ot.find_tables(img)
    if not tables:
        raise RuntimeError("OCR: no ruled table")
    t = tables[0]
    xs = ot.vlines(a, t)
    cols = [(x0, x1) for x0, x1 in zip(xs, xs[1:]) if x1 - x0 > 8]
    num = cols[n_label:]
    if len(num) != len(COLS):
        raise RuntimeError(f"OCR: {len(num)} numeric columns, expected {len(COLS)}")
    rx = num[COLS.index("flat")]
    ys = [y for y in t["hlines"] if ot._has_rule(a, y, *rx)]
    bands = list(zip(ys, ys[1:]))[n_head_lines - 1:]
    out = [[None] * len(num) for _ in bands]
    raw = [[""] * len(num) for _ in bands]
    merged = []
    for c, (x0, x1) in enumerate(num):
        if not any(ot._has_rule(a, b[0], x0, x1) for b in bands[1:]):
            merged.append(c)
            y0, y1 = bands[0][0], bands[-1][1]
            sub = a[y0 + 3:y1 - 2, x0 + 3:x1 - 2]
            ink = np.where(sub.mean(axis=1) > 0.01)[0]
            v, s = (None, "")
            if len(ink):
                v, s = ot.ocr_num(img, (x0, y0 + 3 + ink.min() - 4, x1, y0 + 3 + ink.max() + 5), scale)
            for r in range(len(bands)):
                out[r][c], raw[r][c] = v, s
        else:
            for r, (y0, y1) in enumerate(bands):
                out[r][c], raw[r][c] = ot.ocr_num(img, (x0, y0, x1, y1), scale)
    return out, raw, merged


def _text_grid(path):
    i, text, grid = nw.find_page_table(path, [r"代理购电工商业用户电价表"], head_len=800)
    nw.check_month(text, os.path.basename(path)[:7])
    rows = [r for r in grid if len(r) >= 17 and pc.voltage_rank(cms._clean(r[2]))]
    vals = [[cms.cell_num(c) for c in r[3:17]] for r in rows]
    for c in range(len(COLS)):          # merged cells: one value in the first row of the span
        col = [v[c] for v in vals]
        if sum(x is not None for x in col) == 1 and col[0] is not None:
            for v in vals:
                v[c] = col[0]
    return vals


def _check(rec):
    """The table's own arithmetic, exact to the printed 6 decimals."""
    base = rec["tdp"] + rec["system_op"] + rec["gov_fund"]
    for k, p, l in (("peak", "p_peak", "ll_peak"), ("flat", "p_flat", "ll_flat"), ("valley", "p_valley", "ll_valley")):
        if None in (rec[k], rec[p], rec[l]) or abs(rec[p] + rec[l] + base - rec[k]) > 1.5e-6:
            return f"{k}={rec[k]} != {p}+{l}+tdp+sys+fund"
    return None


def parse(ann, template=None, log=print):
    def run():
        path = ann["file_path"]
        if path.endswith(".pdf") and _has_text(path):
            vals = _text_grid(path)
            method = "pdfplumber text table (PDF with text layer)"
        else:
            img = _image(path)
            vals = None
            for scale in (1.5, 2.0, 2.5):
                v, raws, merged = ocr_grid(img, scale=scale, log=log)
                errs = _validate(v)
                if not errs:
                    vals = v
                    break
                log(f"  OCR scale {scale}: {errs[:3]}")
            if vals is None:
                raise RuntimeError(f"OCR failed arithmetic/layout checks: {errs[:3]}")
            method = f"OCR (tesseract) of {'PDF page-1 image' if path.endswith('.pdf') else 'PNG poster'}, scale {scale}"
        errs = _validate(vals)
        if errs:
            raise RuntimeError(f"table checks failed: {errs[:3]}")
        out = []
        for v, (tt, volt) in zip(vals, LAYOUT[len(vals)]):
            rec = dict(zip(COLS, v))
            out.append({"month": ann["month"], "category": "工商业用电", "tariff_type": tt, "voltage": volt,
                        "voltage_rank": pc.voltage_rank(volt), "energy_price": rec["flat"],
                        "purchase_price": rec["p_flat"], "line_loss": rec["ll_flat"], "tdp": rec["tdp"],
                        "system_op": rec["system_op"], "gov_fund": rec["gov_fund"], "sharp": None,
                        "peak": rec["peak"], "flat": rec["flat"], "valley": rec["valley"], "deep_valley": None,
                        "demand_price": rec["demand_price"] if tt == "两部制" else None,
                        "capacity_price": rec["capacity_price"] if tt == "两部制" else None,
                        "_p_peak": rec["p_peak"], "_p_valley": rec["p_valley"],
                        "_ll_peak": rec["ll_peak"], "_ll_valley": rec["ll_valley"]})
        ann["parse_method"] = method
        ann["effective_from"], ann["effective_to"] = cms.effective_dates("", ann["month"])
        return out

    rows = ot.cached_parse(ann, REGION["code"], run, version=OCR_VERSION)
    r0 = rows[0]
    ann["period_prices"] = {"purchase": {"peak": r0["_p_peak"], "flat": r0["purchase_price"], "valley": r0["_p_valley"]},
                            "line_loss": {"peak": r0["_ll_peak"], "flat": r0["line_loss"], "valley": r0["_ll_valley"]}}
    ann["effective_from"], ann["effective_to"] = cms.effective_dates("", ann["month"])
    return rows


def _has_text(path):
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        return len((pdf.pages[0].extract_text() or "").strip()) > 50


def _validate(vals):
    errs = []
    n = len(vals)
    if n not in LAYOUT:
        return [f"{n} data rows, expected 7 or 9"]
    for i, (v, tdp) in enumerate(zip(vals, TDP[n])):
        rec = dict(zip(COLS, v))
        if rec["tdp"] is None or abs(rec["tdp"] - tdp) > 1e-9:
            errs.append(f"row {i + 1} tdp {rec['tdp']} != regulated {tdp}")
        e = _check(rec)
        if e:
            errs.append(f"row {i + 1} {e}")
        tt = LAYOUT[n][i][0]
        if tt == "两部制" and (rec["demand_price"] is None or rec["capacity_price"] is None):
            errs.append(f"row {i + 1} 需量/容量 blank")
    return errs


def postprocess(parsed, anns):
    return ot.regulated_consensus(parsed, anns, period_key=lambda m, rows: len(rows))


def extra_validate(rows):
    fails = []
    for r in rows:
        rec = {"tdp": r["tdp"], "system_op": r["system_op"], "gov_fund": r["gov_fund"], "peak": r["peak"],
               "flat": r["flat"], "valley": r["valley"], "p_peak": r.get("_p_peak"), "p_flat": r["purchase_price"],
               "p_valley": r.get("_p_valley"), "ll_peak": r.get("_ll_peak"), "ll_flat": r["line_loss"],
               "ll_valley": r.get("_ll_valley")}
        e = _check(rec)
        if e:
            fails.append({"month": r["month"], "category": r["category"], "tariff_type": r["tariff_type"],
                          "voltage": r["voltage"], "check": "period_formula", "detail": e})
    return fails


# ---------------------------------------------------------------- TOU: 甘发改价格〔2024〕424号
TOU_424 = ("https://www.95598.cn/omg-static/99307021080468606529101523659698",
           "甘肃省发展和改革委员会关于优化调整工商业等用户峰谷分时电价政策有关事项的通知", "甘发改价格〔2024〕424号",
           "2024-07-05", "2024-08-01")


def tou_rules():
    code = REGION["code"]
    doc = nw.tou_doc(code, *TOU_424)
    spec = {"peak": [(6, 8), (18, 23)], "flat": [(23, 6), (8, 10), (16, 18)], "valley": [(10, 16)]}
    rules = [(list(range(1, 13)), "normal", cms.segs_from_hours(spec))]
    return doc, cms.tou_rows(code, doc, rules, TOU_ID_BASE)


TOU_NOTES = [
    "TOU periods per 甘发改价格〔2024〕424号 as restated in note 3 (2026-01..07) / note 2 (2026-08..10) of every monthly notice: "
    "高峰 6:00-8:00、18:00-23:00; 平 23:00-6:00(次日)、8:00-10:00、16:00-18:00; 低谷 10:00-16:00 (all months; no 尖峰/深谷).",
    "Market-based TOU: the 峰/谷 spread of the 上网电价 (代理购电价格) is formed by the market (甘肃中长期/现货交易规则), 上网环节线损电价 "
    "floats with it; 输配电价, 系统运行费, 基金 do not float.  The table therefore prints 代理购电价格 and 线损 per period and no 非分时 price: "
    "energy_price = 平时段 分时电度电价, purchase_price / line_loss = 平时段 values; 高峰/低谷 period prices in announcements.json.",
    "2025-11 and 2025-12: no 代理购电 announcement in the 95598.cn 甘肃 listing (series starts 2026-01) and no other official free copy found → missing.",
    "4th TDP period from 2026-08 (发改价格〔2026〕1077号): 单一制 adds 110 / 220千伏及以上, TDP changes.",
    "Source: 95598.cn omg-static copies; images OCR'd (tesseract) and accepted only when every row satisfies 10=1+4+7+8+9, "
    "11=2+5+7+8+9, 12=3+6+7+8+9 exactly and 电度输配电价 equals the period's regulated value row for row.",
    "TOU policy 甘发改价格〔2024〕424号: signed 2024-07-05, effective 2024-08-01 (OCR of the official scan on 95598.cn); 2. 时段划分 is one "
    "all-year schedule; 峰谷价差 ≥ 3:1, float ratios from 2025 set separately (market-formed, see above).",
]
