"""国网河南省电力公司 代理购电工商业用户电价 (河南).

Sources
* Monthly notices "国网河南省电力公司YYYY年M月代理购电价格公告" are listed on 95598.cn › 执行的电价和收费标准 (region 河南),
  URLs in data/raw/henan/seed.json.  Those PDFs are useless as text: the table numbers are outlined glyphs (the text layer only
  holds stale CorelDRAW layers such as "关于2025年6月…") and 2026-02/04/06/07 are a single 1149-px bitmap.
* 平顶山市人民政府 (pds.gov.cn › 价格收费) reposts the identical notice as a text PDF ("…公示表-水印版.pdf", KaiTi
  diagonal "YYYY年M月D日发布" watermark).  Where that repost exists (2026-01..2026-08, seed field text_pdf_url) it is parsed
  as text (watermark glyphs filtered by font/size), and policy_doc.source_url points to it.
* 2026-09, 2026-10 have no reachable text repost (no pds.gov.cn PDF under any guessed path) → the
  95598 vector PDF is rendered at 5000 px and OCR'd, cross-validated with _central_util.affine_solve (Σ components = 电度 =
  平段; TOU − 电度 affine in 输配电价 across the month's rows).
* Columns (both paths): 电度用电价格, 代理购电价格, 上网环节线损电价, 电度输配电价, 政府性基金及附加, 系统运行费折价, 尖峰,
  高峰, 平时段, 低谷, 最大需量, 变压器容量.  尖峰 printed in 尖峰 months only (1、7、8、12月 per 豫发改价管〔2024〕283号).
* Layout: to 2026-07 8 rows (单一制 不满1 / 1-10 / 35-110千伏以下 / 110千伏及以上; 两部制 1-10 / 35-110千伏以下 / 110 /
  220千伏及以上); from 2026-08 (4th TDP period) 9 rows (单一制 adds 220千伏及以上, 35 / 110 split).
"""
import json
import os
import re

import pdfplumber

from . import _central_util as cu
from . import ocr_table as ot
from . import sgcc_cms as cms
from .. import parse_common as pc

REGION = {"code": "henan", "name": "河南", "grid": "sgcc", "province": "河南省"}
CODE = REGION["code"]
OCR_VERSION = 1
P4_FROM = "2026-08"
TOU_ID_BASE = 22000
_months = cu.pc_months
COLS = ["energy_price", "purchase_price", "line_loss", "tdp", "gov_fund", "system_op", "sharp", "peak", "flat",
        "valley", "demand_price", "capacity_price"]
SHARED = ["purchase_price", "line_loss", "gov_fund", "system_op"]
LAYOUT_P3 = [("单一制", "不满1千伏"), ("单一制", "1-10千伏"), ("单一制", "35-110千伏以下"), ("单一制", "110千伏及以上"),
             ("两部制", "1-10千伏"), ("两部制", "35-110千伏以下"), ("两部制", "110千伏"), ("两部制", "220千伏及以上")]
LAYOUT_P4 = [("单一制", "不满1千伏"), ("单一制", "1-10(20)千伏"), ("单一制", "35千伏"), ("单一制", "110千伏"),
             ("单一制", "220千伏及以上"), ("两部制", "1-10(20)千伏"), ("两部制", "35千伏"), ("两部制", "110千伏"),
             ("两部制", "220千伏及以上")]


def discover(start, end, log=print):
    return cu.seed_discover(CODE, start, end, log)


def download(ann, log=print):
    p = cu.download(CODE, ann, log)
    if ann.get("text_pdf_url"):
        tp = os.path.join(pc.DATA, "raw", CODE, f"{ann['month']}_pds.pdf")
        if not (os.path.exists(tp) and os.path.getsize(tp) > 1000):
            r = pc.http_get(ann["text_pdf_url"], timeout=90)
            if not r.content.startswith(b"%PDF"):
                raise RuntimeError(f"pds repost is not a PDF: {ann['text_pdf_url']}")
            open(tp, "wb").write(r.content)
        ann["text_pdf_path"] = tp
        ann["text_pdf_sha256"] = pc.sha256_file(tp)
    return p


def policy_doc_row(ann):
    d = cu.policy_doc_row(CODE, ann)
    if ann.get("text_pdf_path"):
        d.update(id=pc.doc_id(ann["text_pdf_url"]), source_url=ann["text_pdf_url"],
                 file_path=os.path.relpath(ann["text_pdf_path"], pc.ROOT), sha256=ann["text_pdf_sha256"],
                 title=ann["title"] + "（平顶山市人民政府转载）")
    return d


def _clean_page(path):
    pg = pdfplumber.open(path).pages[0]
    return pg.filter(lambda o: o.get("object_type") != "char" or
                     (o.get("size", 0) < 30 and "KaiTi" not in (o.get("fontname") or "")))


def _parse_text(ann, log):
    pg = _clean_page(ann["text_pdf_path"])
    txt = pg.extract_text() or ""
    m = re.search(r"执行时间[：:]\s*(\d{4})年(\d{1,2})月", txt)
    if not m or f"{int(m.group(1)):04d}-{int(m.group(2)):02d}" != ann["month"]:
        raise RuntimeError(f"pds repost 执行时间 mismatch: {m.group(0) if m else None}")
    grids = [t for t in pg.extract_tables() if any("电压等级" in (c or "") for r in t[:3] for c in r)]
    if not grids:
        raise RuntimeError("no tariff table in pds repost")
    rows = cms.parse_grid(grids[0], ann["month"], nd=9)
    layout = LAYOUT_P4 if ann["month"] >= P4_FROM else LAYOUT_P3
    if [(r["tariff_type"], pc.voltage_rank(r["voltage"])) for r in rows] != [(t, pc.voltage_rank(v)) for t, v in layout]:
        raise RuntimeError(f"unexpected row layout {[(r['tariff_type'], r['voltage']) for r in rows]}")
    for r in rows:
        r["voltage"] = r["voltage"].replace("（", "(").replace("）", ")")
    cu.finish_rows(rows)
    cu.sanity(rows)
    ann["parse_method"] = "text (pdfplumber) of the 平顶山市人民政府 repost, watermark glyphs filtered"
    ann["effective_from"], ann["effective_to"] = cms.effective_dates(txt, ann["month"])
    return rows


def _ocr(ann, log):
    img = cu.page_image(ann["file_path"], target_w=5000)
    tables, masks = cu.find_tables(img)
    t = next((x for x in tables if len(x["hlines"]) >= 10 and x["top"] < img.height * 0.6), None)
    if t is None:
        raise RuntimeError(f"main table not found {[(x['top'], len(x['hlines'])) for x in tables]}")
    head = cu.head_text(img, t, frac=0.03)
    ann["ocr_title"] = head[-80:]
    m = re.search(r"(\d{4})年(\d{1,2})月1日", head)
    if m and f"{int(m.group(1)):04d}-{int(m.group(2)):02d}" != ann["month"]:
        raise RuntimeError(f"执行时间 OCR says {m.group(0)}, expected {ann['month']}")
    tab = cu.read(img, t, masks, scale=1.0)
    data = tab["data"]
    # drop trailing all-empty columns (page border)
    n = len(data[0][0])
    while n > len(COLS) and all(nums[n - 1] is None for nums, _ in data):
        n -= 1
    if n != len(COLS):
        raise RuntimeError(f"OCR: {n} numeric columns, expected {len(COLS)}")
    tab["data"] = [(nums[:n], raws[:n]) for nums, raws in data]
    layout = LAYOUT_P4 if ann["month"] >= P4_FROM else LAYOUT_P3
    rows, raws = cu.rows_from_table(tab, COLS, layout, ann["month"])
    fixes = cu.affine_solve(rows, raws, SHARED, ["sharp", "peak", "valley"], nd=9, tol=1.5e-6, log=log)
    fixes += _reread_regulated(img, tab, rows)
    cu.finish_rows(rows)
    cu.sanity(rows)
    ann["ocr_corrections"] = fixes
    ann["parse_method"] = f"OCR (tesseract) of the 95598.cn PDF rendered at 5000 px + arithmetic cross-check ({len(fixes)} repairs)"
    ann["effective_from"], ann["effective_to"] = cms.effective_dates(head, ann["month"])
    return rows


def _reread_regulated(img, tab, rows):
    """需量/容量 cells are small integers/one-decimal numbers the line OCR often drops: re-read 两部制 cells ×2."""
    fixes = []
    cols = tab["cols"]
    for k, r in enumerate(rows):
        if r["tariff_type"] != "两部制":
            continue
        y0, y1 = tab["rows"][k]
        for f, (x0, x1) in (("demand_price", cols[len(COLS) - 2]), ("capacity_price", cols[len(COLS) - 1])):
            if r.get(f) is not None:
                continue
            cell = img.crop((x0 + 6, y0 + 6, x1 - 6, y1 - 6))
            txt = ot._tess(cell, "eng", psm=7, extra="-c tessedit_char_whitelist=0123456789.")
            if re.fullmatch(r"\d{2}(\.\d)?", txt or ""):
                r[f] = float(txt)
                fixes.append(f"两部制 {r['voltage']} {f}: blank in line OCR, cell re-read '{txt}'")
    return fixes


def parse(ann, template=None, log=print):
    if ann.get("text_pdf_path"):
        return _parse_text(ann, log)
    return cu.ocr_cache(CODE, ann, lambda: _ocr(ann, log), OCR_VERSION)


def _period(m, rows=None):
    return "P4" if m >= P4_FROM else "P3"


def postprocess(parsed, anns):
    log = cu.regulated_postprocess(parsed, anns, _period)
    # 2026-10 两部制 35千伏 高峰: the line OCR left the cell blank and affine_solve could not pin it (needs >= 2 cells).
    # Image data/raw/henan/2026-10.pdf prints 0.959899295 (found by energydc cross-check 2026-10-11, verified on the
    # official PDF; crop work/compare/evidence/henan_2026-10_table.png). It also fits the month's affine relation
    # 高峰 − 电度 = 0.27965592 + 0.72·输配 (0.580019375 + 0.27965592 + 0.72·0.1392 = 0.959899295).
    for r in parsed.get("2026-10", []):
        if (r["tariff_type"] == "两部制" and r["voltage"] == "35千伏" and r.get("peak") is None
                and abs((r.get("energy_price") or 0) - 0.580019375) < 1e-9):
            r["peak"] = 0.959899295
            msg = "两部制 35千伏 peak: blank in OCR -> 0.959899295 (as printed; image-verified fix)"
            anns["2026-10"].setdefault("ocr_corrections", []).append(msg)
            log.append(f"2026-10 {msg}")
    return log


# ---------------------------------------------------------------- TOU (豫发改价管〔2024〕283号)
TOU_283 = ("https://fgw.henan.gov.cn/2024/05-29/3000232.html",
           "河南省发展和改革委员会关于调整工商业分时电价有关事项的通知", "豫发改价管〔2024〕283号",
           "2024-05-29", "2024-06-01", None)


def tou_rules():
    S = cms.segs_from_hours
    d = cms.tou_doc(CODE, *TOU_283)
    winter = {"peak": [(16, 24)], "valley": [(0, 7)]}
    shoulder = {"peak": [(16, 24)], "valley": [(0, 6), (11, 14)]}
    rules = [([1, 12], "normal", S(dict(winter, sharp=[(17, 19)]))),
             ([2], "normal", S(winter)),
             ([3, 4, 5, 9, 10, 11], "normal", S(shoulder)),
             ([6], "normal", S(winter)),
             ([7, 8], "normal", S(dict(winter, sharp=[(20, 23)])))]
    return d, cms.tou_rows(CODE, d, rules, TOU_ID_BASE)


TOU_NOTES = [
    "TOU periods per 河南省发改委《关于调整工商业分时电价有关事项的通知》豫发改价管〔2024〕283号 (2024-05-29, effective "
    "2024-06-01; cited in note 2 of every notice in the window; reposted on 95598.cn 2026-09-01): 1、2、12月 峰 16-24 (尖峰 17-19 in "
    "1、12月), 谷 0-7; 3-5、9-11月 峰 16-24, 谷 0-6、11-14; 6-8月 峰 16-24 (尖峰 20-23 in 7、8月), 谷 0-7; other hours 平. No 深谷.",
    "Float per 283号: 峰:平:谷 = 1.72 : 1 : 0.45, 尖峰 = 1.2 × the 峰 float, applied to (代理购电价格 + 输配电价); a 2026-01 DRC "
    "征求意见稿 proposed that 输配电价 of 代理购电 users stop floating, and the TOU spread's dependence on 输配电价 indeed varies between "
    "notices. Prices are stored exactly as printed; 尖峰 only where printed (1、7、8、12月).",
    "Sources: 2025-11..2026-08 parsed as text from the identical notice reposted by 平顶山市人民政府 (pds.gov.cn 价格收费, watermarked "
    "PDF); 2025-11, 2025-12, 2026-09, 2026-10 OCR'd from the 95598.cn PDF (outlined glyphs, no text layer) and cross-validated against "
    "the notice arithmetic.",
]
