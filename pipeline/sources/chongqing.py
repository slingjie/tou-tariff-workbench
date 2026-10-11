"""国网重庆市电力公司 代理购电工商业用户电价 — thin config over sgcc_cms + ocr_table.

Quirks
* Column col2680 ("供电企业执行的电价和收费标准"), titles '代理购电价格公示表-YYYY年M月(发布)'.
* Each article is an image gallery: the 2 JPGs (1785×1262 px) are usually the SAME rendering of the notice
  (信息表 + 工商业用户电价表); duplicates are dropped by sha256.  No text layer anywhere → OCR.
* OCR (tesseract eng, digits whitelist, ×3 upscale) is cross-validated per row against the published relations
  (Σ components = 非分时电度电价 = 平段; 高峰 = 平段 + 60%·(代理购电价格+输配电价); 低谷 = 平段 − 62%·(…);
  尖峰 = 高峰 + 20%) and single-cell misreads are repaired only when ≥ 2 other cells agree (logged in
  announcements.json → ocr_corrections).
* Row labels come from LAYOUTS (verified visually), chosen by row count; TDP must decrease with voltage.
"""
from . import sgcc_cms as cms
from . import ocr_table as ocr
from .. import parse_common as pc
import os

REGION = {"code": "chongqing", "name": "重庆", "grid": "sgcc", "province": "重庆市"}
CMS = cms.CMSRegion(REGION, "http://www.cq.sgcc.com.cn", "2680",
                    title_res=[r"代理购电价格公示表[-—－]?(\d{4})年(\d{1,2})月"], max_pages=3,
                    alt_hosts=["http://www.cq.sgcc.cn"])
_months = cms.months_between
policy_doc_row = CMS.policy_doc_row
TOU_ID_BASE = 3000

# Months whose cq.sgcc.com.cn image cannot be read reliably but for which the SAME notice was found as a
# text-layer PDF on State Grid's national customer-service site 95598.cn (official, free).  These replace the
# image as source of record for that month (the gallery image URL is kept in ann["cms_img_urls"]).
ALT_PDF = {
    # 2025-11: cq.sgcc.com.cn gallery JPG returns 404 on both hosts -> 95598.cn text PDF (执行时间 2025-11-01..11-30)
    "2025-11": "https://www.95598.cn/omg-static/99307090136555352674600002747812",
    # 2026-03: JPG is a 1262×892 re-render too blurry for tesseract -> 95598.cn text PDF (执行时间 2026-03-01..03-31)
    "2026-03": "https://www.95598.cn/omg-static/99307090116759420310901934550254",
    "2026-05": "https://www.95598.cn/omg-static//omg-static/99304271882705328964600637163120.pdf",
}
# Local file names of the 95598 copies (default f"{month}_95598.pdf").
ALT_LOCAL = {"2025-11": "95598_2025-11.pdf", "2026-03": "95598_2026-03.pdf"}


def _apply_alt(ann):
    url = ALT_PDF.get(ann["month"])
    if not url or ann.get("file_url") == url:
        return ann
    ann["cms_img_urls"] = ann.get("img_urls") or ann.get("all_files") or []
    for k in ("img_urls", "file_paths", "img_sha256", "file_path", "sha256", "error", "parse_method",
              "ocr_corrections", "ocr_voltage_digits"):
        ann.pop(k, None)
    ann.update(file_kind="pdf", file_ext="pdf", file_url=url, alt_source="95598.cn (国家电网 official text PDF)")
    return ann


def discover(start, end, log=print):
    return [_apply_alt(a) for a in CMS.discover(start, end, log=log)]


def download(ann, log=print):
    _apply_alt(ann)
    if ann["month"] in ALT_PDF:
        p = CMS._fetch_to(ann["file_url"], os.path.join(CMS.raw_dir, ALT_LOCAL.get(ann["month"], f"{ann['month']}_95598.pdf")),
                          [b"%PDF"], log)
        ann["file_path"] = p
        ann["sha256"] = pc.sha256_file(p)
        return p
    return CMS.download(ann, log=log)

COLUMNS = ["energy_price", "purchase_price", "line_loss", "tdp", "gov_fund", "system_op",
           "sharp", "peak", "flat", "valley", "demand_price", "capacity_price"]
LAYOUTS = {
    # 4th TDP period (from 2026-08): 单一制 5 rows + 两部制 4 rows
    9: [("单一制", "不满1千伏"), ("单一制", "1-10千伏"), ("单一制", "35千伏"), ("单一制", "110千伏"),
        ("单一制", "220千伏及以上"), ("两部制", "1-10千伏"), ("两部制", "35千伏"), ("两部制", "110千伏"),
        ("两部制", "220千伏及以上")],
    # 3rd TDP period (to 2026-07): 单一制 4 rows (110千伏及以上 merged) + 两部制 4 rows
    8: [("单一制", "不满1千伏"), ("单一制", "1-10千伏"), ("单一制", "35千伏"), ("单一制", "110千伏及以上"),
        ("两部制", "1-10千伏"), ("两部制", "35千伏"), ("两部制", "110千伏"), ("两部制", "220千伏及以上")],
}
INFO_ROWS = {"purchase_price": 4, "line_loss": 7, "system_op": 8}   # 序号 in 代理购电价格信息表
COEF = {"peak": 0.60, "valley": 0.62, "sharp_on_peak": 0.20}


OCR_VERSION = 3
LINE_THR = 200   # rules are 1-px anti-aliased grey in these JPGs


def parse(ann, template=None, log=print):
    if ann.get("file_path", "").lower().endswith(".pdf"):
        return _parse_pdf(ann)
    return ocr.cached_parse(ann, REGION["code"], lambda: _parse(ann, log), OCR_VERSION)


def _parse_pdf(ann):
    pages = cms.pdf_tables(ann["file_path"])
    grids = [g for _, _, ts in pages for g in ts if "电压等级" in cms.grid_text(g)]
    if not grids:
        raise RuntimeError("tariff table (电压等级) not found in PDF")
    rows = cms.parse_grid(grids[0], ann["month"])
    for r in rows:
        r["category"] = "工商业用电"
    ann["effective_from"], ann["effective_to"] = cms.effective_dates("".join(t for _, t, _ in pages), ann["month"])
    ann["parse_method"] = "pdfplumber text table (95598.cn PDF)"
    return rows


def _parse(ann, log=print):
    """Some months are re-rendered at 1262×892 px (2026-03, 2026-05): rules get fainter, so line detection
    is retried at lighter thresholds and a larger upscale until the row count matches a layout."""
    paths = ann.get("file_paths") or [ann["file_path"]]
    last = None
    for path in paths:
        img = ocr.load_image(path)
        small = img.width < 1500
        for thr in ((200, 215, 230) if not small else (215, 230)):
            tables, a = ocr.find_tables(img, thr=thr)
            t = ocr.pick_table(img, tables, ["工商业用户电价表", "用户电价表"])
            if t is None:
                last = RuntimeError(f"tariff table not found in {path}: titles {[x.get('title_ocr', '')[:30] for x in tables]}")
                continue
            try:
                tab = ocr.read_table(img, t, a, scale=4.0 if small else 3.0, log=log)
            except RuntimeError as e:
                last = e
                continue
            layout = LAYOUTS.get(len(tab["data"]))
            if not layout:
                last = RuntimeError(f"{len(tab['data'])} data rows at line threshold {thr} – no layout")
                continue
            info = {}
            try:
                iv = ocr.read_info_table(img, tables, a, scale=4.0 if small else 3.0, log=log)
                info = {f: iv[k] for f, k in INFO_ROWS.items() if k in iv}
            except RuntimeError:
                pass
            try:
                rows, fixes = ocr.solve_rows(tab, COLUMNS, layout, ann["month"], COEF, base_kind="P+tdp", log=log,
                                             info=info)
            except RuntimeError as e:
                last = e
                continue
            for r in rows:
                r["category"] = "工商业用电"
            bad = [f for f in pc.validate_rows(rows) + extra_validate(rows)
                   if f["check"] in ("components_sum", "components_missing", "tou_formula", "tou_order")]
            if len({(f["tariff_type"], f["voltage"]) for f in bad}) > len(rows) // 4:
                last = RuntimeError(f"OCR unreliable at rules@{thr}: {len(bad)} formula failures, e.g. {bad[0]['check']} {bad[0]['detail']}")
                continue
            ann["effective_from"], ann["effective_to"] = cms.effective_dates(t.get("title_ocr", ""), ann["month"])
            ann["parse_method"] = f"ocr(tesseract eng ×{4 if small else 3}, rules@{thr}, {path.rsplit('/', 1)[-1]})"
            ann["ocr_corrections"] = fixes
            ann["ocr_voltage_digits"] = tab["voltage_digits"]
            if fixes:
                log(f"  OCR repairs {ann['month']}: {len(fixes)}")
            return rows
    raise last or RuntimeError("no image parsed")


def _expected(r):
    B = r["purchase_price"] + r["tdp"]
    f = r["energy_price"]
    exp = {"flat": f, "peak": f + 0.6 * B, "valley": f - 0.62 * B}
    if r.get("sharp") is not None:
        exp["sharp"] = exp["peak"] + 0.2 * 1.6 * B
    return exp


def extra_validate(rows):
    fails = cms.tou_formula_check(rows, _expected, tol=0.0006)
    # TDP must strictly decrease with voltage inside a tariff block (catches label/OCR shifts)
    for (m, tt), g in _group(rows).items():
        g = sorted(g, key=lambda r: r["voltage_rank"])
        for lo, hi in zip(g, g[1:]):
            if lo.get("tdp") is not None and hi.get("tdp") is not None and not hi["tdp"] < lo["tdp"]:
                fails.append({"month": m, "category": hi["category"], "tariff_type": tt, "voltage": hi["voltage"],
                              "check": "tdp_order", "detail": f"tdp {hi['tdp']} !< {lo['tdp']}"})
    return fails


def _group(rows):
    g = {}
    for r in rows:
        g.setdefault((r["month"], r["tariff_type"]), []).append(r)
    return g


def postprocess(parsed, anns):
    log = ocr.regulated_consensus(parsed, anns, fields=("tdp", "demand_price", "capacity_price"))
    # 需量/容量 prices are regulated per voltage; inside one TDP period (= one row layout) the value seen in the
    # majority of months wins.  Checked against the images: 2026-08 两部制 35千伏 is printed 35.2 (OCR read 38.2).
    # 2026-02: OCR misread 政府性基金 and the residual step then overwrote a correct 线损; the two errors cancel in the
    # sum check. Image data/raw/chongqing/2026-02_1.jpg prints 线损 0.018634 and 基金 0.047694 (found by energydc
    # cross-check 2026-10-11, verified on the official image; crops work/compare/evidence/cq2602_*.png).
    for r in parsed.get("2026-02", []):
        if abs((r.get("line_loss") or 0) - 0.048634) < 1e-7 and abs((r.get("gov_fund") or 0) - 0.017694) < 1e-7:
            r["line_loss"], r["gov_fund"] = 0.018634, 0.047694
            log.append(f"2026-02 {r['tariff_type']} {r['voltage']}: line_loss/gov_fund swapped-digit fix per image")
    return log


# ---------------------------------------------------------------- TOU (渝发改规范〔2021〕14号)
TOU_14 = ("https://fzggw.cq.gov.cn/zwgk/zfxxgkml/zcwj/xzgfxwj/sfzggwxzgfxwj/202112/t20211209_10119678.html",
          "重庆市发展和改革委员会关于进一步完善我市分时电价机制有关事项的通知", "渝发改规范〔2021〕14号",
          "2021-12-09", "2022-01-01")


def _spec(sharp):
    s = {"peak": [(11, 17), (20, 22)], "flat": [(8, 11), (17, 20), (22, 24)], "valley": [(0, 8)]}
    if sharp:
        s["sharp"] = [(12, 14)]
    return s


def tou_rules():
    code = REGION["code"]
    d = cms.tou_doc(code, *TOU_14)
    rules = [([1, 7, 8, 12], "normal", cms.segs_from_hours(_spec(True))),
             ([2, 3, 4, 5, 6, 9, 10, 11], "normal", cms.segs_from_hours(_spec(False)))]
    return d, cms.tou_rows(code, d, rules, TOU_ID_BASE)


TOU_NOTES = [
    "TOU periods per 渝发改规范〔2021〕14号 (effective 2022-01-01, still cited by every 2025-11..2026-10 notice: "
    "'时段划分按照省级价格主管部门相关政策执行'): 高峰 11-17、20-22; 平段 8-11、17-20、22-24; 低谷 0-8; "
    "尖峰 12-14 in 7、8、12、1月 (inside 高峰). No holiday/weekend variant, no 深谷.",
    "Scope (14号): 10千伏及以上 and 受电变压器 ≥100kVA 大工业 / 普非工业 动力用电; 不满1千伏 rows print no TOU prices. "
    "普非工业 users may opt for 平均分时电价 = 平段 + 0.03 元/kWh (not stored).",
    "Float: 高峰 = 平段 + 60%·B, 低谷 = 平段 − 62%·B, 尖峰 = 高峰 × 1.2 on the floated part, B = 代理购电价格 + 电度输配电价 "
    "(基金、线损、系统运行费 do not float). 尖峰 printed only in 1、7、8、12月.",
    "Since 2026-03-01 (发改能源规〔2025〕1656号 / 重庆电力中长期市场实施细则) direct market participants no longer use the "
    "government TOU; agency-purchase (代理购电) customers, whose tariffs these are, still do.",
    "Layout: 3rd TDP period (to 2026-07) 单一制 4 rows (110千伏及以上 merged) + 两部制 4 rows; 4th period (2026-08 →, "
    "重庆电网销售电价表及第四监管周期输配电价表 posted 2026-08-03) 单一制 5 rows + 两部制 4 rows.",
    "Source: cq.sgcc.com.cn col2680 image galleries, OCR'd (tesseract) and cross-validated against the published float "
    "formula; 2025-11 (official image 404), 2026-03 (image too low-resolution for OCR) and 2026-05 taken from the "
    "State Grid text PDFs on 95598.cn; 2026-01 OCR verified against its 95598.cn text PDF (0 differences).",
]
