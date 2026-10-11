"""国网黑龙江省电力有限公司 代理购电工商业用户电价 — seed (95598.cn) + text PDF / OCR (2026-01).

Quirks
* hl.sgcc.com.cn answers HTTP 412 (JS anti-bot) to curl; its two 电价 columns stop at 2025-03 (新闻中心 › 供电企业执行的电价和
  标准) and 2025-08 (营销服务 › 供电企业执行的电价和标准).  On 95598.cn the 执行的电价和收费标准 listing for 黑龙江 is "功能正在维护
  升级" (2026-10-11), so only what the 网上国网 web exposes is available: the 黑龙江 homepage news list (latest six notices,
  2026-05..2026-10, text PDFs under omg-static) and one 资讯 article for 2026-01 (omg-static/…html with the 附表1/附表2 JPGs).
  2025-11, 2025-12, 2026-02, 2026-03, 2026-04 were not found on any official free page → missing (see seed.json "missing").
* Table: 9 rows – 单一制 不满1 / 1-10(20) / 35 / 110(66) / 220及以上, 两部制 1-10(20) / 35 / 110(66) / 220及以上 (unchanged in
  the window).  Columns: 非分时, 代理购电价格(电量上网电价), 线损, 输配, 系统运行费, 基金, 尖峰 (blank – 暂停执行), 高峰, 平, 低谷,
  需量, 容量.  高峰/低谷 = 平 ± 50%·代理购电价格 (exact to 6 decimals).
"""
import json, os, re

from . import sgcc_cms as cms
from . import ocr_table as ocr
from .. import parse_common as pc

REGION = {"code": "heilongjiang", "name": "黑龙江", "grid": "sgcc", "province": "黑龙江省"}
RAW = os.path.join(pc.DATA, "raw", REGION["code"])
TOU_ID_BASE = 43000
OCR_VERSION = 2
_months = cms.months_between

COLUMNS = ["energy_price", "purchase_price", "line_loss", "tdp", "system_op", "gov_fund",
           "sharp", "peak", "flat", "valley", "demand_price", "capacity_price"]
V5 = ["不满1千伏", "1-10(20)千伏", "35千伏", "110(66)千伏", "220千伏及以上"]
LAYOUT = [("单一制", v) for v in V5] + [("两部制", v) for v in V5[1:]]
UP = 0.5


def discover(start, end, log=print):
    seed = json.load(open(os.path.join(RAW, "seed.json"), encoding="utf-8"))
    wanted = set(_months(start, end))
    out = [dict(a, discovered_via="seed", file_kind=a["file_ext"]) for a in seed["articles"] if a["month"] in wanted]
    log(f"  seed: {len(out)} months; missing (no official source found): {sorted(seed.get('missing', {}))}")
    return sorted(out, key=lambda a: a["month"])


MAGIC = {"pdf": b"%PDF", "jpg": b"\xff\xd8"}


def download(ann, log=print):
    path = os.path.join(RAW, f"{ann['month']}.{ann['file_ext']}")
    if not (os.path.exists(path) and os.path.getsize(path) > 1000):
        r = pc.http_get(ann["file_url"], timeout=90)
        if not r.content.startswith(MAGIC[ann["file_ext"]]):
            raise RuntimeError(f"unexpected file type: {ann['file_url']}")
        open(path, "wb").write(r.content)
        log(f"  downloaded {os.path.relpath(path, pc.ROOT)}")
    ann["file_path"] = path
    ann["sha256"] = pc.sha256_file(path)
    return path


def policy_doc_row(ann):
    return {"id": pc.doc_id(ann["file_url"]), "region_code": REGION["code"], "kind": "agency_price",
            "title": ann["title"], "doc_no": None, "publish_date": ann.get("publish_date"),
            "effective_from": ann.get("effective_from") or f"{ann['month']}-01", "effective_to": ann.get("effective_to"),
            "source_url": ann["file_url"], "file_path": os.path.relpath(ann["file_path"], pc.ROOT),
            "sha256": ann["sha256"], "fetched_at": pc.now_iso()}


def parse(ann, template=None, log=print):
    if ann["file_ext"] == "pdf":
        for i, text, tables in cms.pdf_tables(ann["file_path"]):
            if "代理购电工商业用户电价表" in re.sub(r"\s+", "", text) and tables:
                ef, et = cms.effective_dates(text, ann["month"])
                if ef[:7] != ann["month"]:
                    raise RuntimeError(f"PDF is for {ef[:7]}, expected {ann['month']}")
                ann["effective_from"], ann["effective_to"] = ef, et
                rows = cms.parse_grid(tables[0], ann["month"])
                for r in rows:
                    r["category"] = "工商业用电"
                ann["parse_method"] = f"pdfplumber page {i + 1}"
                return rows
        raise RuntimeError("代理购电工商业用户电价表 not found")
    return ocr.cached_parse(ann, REGION["code"], lambda: _parse_img(ann, log), OCR_VERSION)


def _parse_img(ann, log=print):
    from PIL import Image
    img = Image.open(ann["file_path"]).convert("L")
    img = img.resize((img.width * 3, img.height * 3), Image.LANCZOS)     # 1340 px render → ×3 for tesseract
    tables, a = ocr.find_tables(img, thr=150)
    t = max(tables, key=lambda tb: len(tb["hlines"]))
    tab = ocr.read_table(img, t, a, scale=1.0, log=log)
    fixes = []
    for i, (nums, raws) in enumerate(tab["data"]):
        for j, raw in enumerate(raws):
            f = COLUMNS[j] if j < len(COLUMNS) else None
            if raw and re.fullmatch(r"[1-9]0\.\d{4,6}", raw) and f not in ("demand_price", "capacity_price"):
                # merged-cell rule read as a leading digit ('10.023772' for 0.023772)
                nums[j] = float(raw[1:]); fixes.append(f"row {i + 1} {f}: '{raw}' -> {nums[j]} (stray leading digit)")
                raws[j] = raw[1:]
            if raw and "." not in raw and f not in ("demand_price", "capacity_price"):
                if re.fullmatch(r"0\d{4}|0\d{6}", raw):
                    nums[j] = float("0." + raw[1:]); fixes.append(f"row {i + 1} {f}: '{raw}' -> {nums[j]}")
                    raws[j] = "0." + raw[1:]
                elif len(raw) < 4:
                    nums[j] = None; raws[j] = ""; fixes.append(f"row {i + 1} {f}: '{raw}' -> blank")
    if len(tab["data"]) != len(LAYOUT) or len(tab["data"][0][0]) != len(COLUMNS):
        raise RuntimeError(f"unexpected shape {len(tab['data'])}×{len(tab['data'][0][0])}")
    rows, fx = ocr.solve_rows(tab, COLUMNS, LAYOUT, ann["month"], {"peak": UP, "valley": UP}, base_kind="month",
                              tol=2e-6, log=log)
    fixes += fx
    for r in rows:
        r["category"] = "工商业用电"
        r["sharp"] = None
        if r["tariff_type"] == "单一制":
            r["demand_price"] = r["capacity_price"] = None
        for f, sg in (("peak", 1), ("valley", -1)):      # exact print rule: 平 ± 0.5·P (P has 6 decimals)
            exp = round(r["flat"] + sg * UP * r["purchase_price"], 6)
            if r.get(f) is not None and r[f] != exp and abs(r[f] - exp) <= 3e-6:
                fixes.append(f"{r['tariff_type']} {r['voltage']} {f}: {r[f]} -> {exp}"); r[f] = exp
    bad = [f for f in pc.validate_rows(rows) if f["check"] in ("components_sum", "components_missing", "tou_order")]
    if bad:
        raise RuntimeError(f"OCR unreliable: {bad[0]}")
    ann["effective_from"], ann["effective_to"] = cms.effective_dates("", ann["month"])
    ann["parse_method"] = "ocr(tesseract eng, 附表1 JPG ×3)"
    ann["ocr_corrections"] = fixes
    return rows


def postprocess(parsed, anns):
    from ._north_util import consensus_ocr_only
    return consensus_ocr_only(parsed, anns, ("tdp", "demand_price", "capacity_price"),
                              lambda m: "P4" if m >= "2026-08" else "P3", lambda a: a.get("file_ext") == "jpg")


def extra_validate(rows):
    def exp(r):
        D = UP * r["purchase_price"]
        return {"flat": r["energy_price"], "peak": r["flat"] + D, "valley": r["flat"] - D}
    return cms.tou_formula_check(rows, exp, tol=1.5e-6)


# ---------------------------------------------------------------- TOU (黑发改价格函〔2024〕291号)
TOU_291 = ("https://www.95598.cn/omg-static//omg-static/99301061503136265824901857003111.pdf",
           "黑龙江省发展和改革委员会关于进一步完善峰谷分时电价政策措施有关事项的通知", "黑发改价格函〔2024〕291号",
           "2024-10-12", "2025-01-01")


def tou_rules():
    code = REGION["code"]
    d = cms.tou_doc(code, *TOU_291)
    spec = {"peak": [(7, 8), (9, 11.5), (15.5, 20)], "valley": [(12, 14), (23.5, 5.5)]}
    rules = [(list(range(1, 13)), "normal", cms.segs_from_hours(spec, default="flat"))]
    return d, cms.tou_rows(code, d, rules, TOU_ID_BASE)


TOU_NOTES = [
    "TOU per 黑发改价格函〔2024〕291号 (印发 2024-10-12, effective 2025-01-01; official scan on 95598.cn, copy "
    "web/downloads/heilongjiang_tou_291.pdf), restated in note 2 of every notice in the window: 高峰 7:00-8:00、9:00-11:30、"
    "15:30-20:00; 低谷 12:00-14:00、23:30-5:30; 其余平段. One rule all year; 尖峰 suspended ('暂停执行尖峰电价'); no 深谷.",
    "Only the 代理购电价格 floats: 高峰 +50%, 低谷 −50% (线损、输配、系统运行费、基金 do not float).",
    "Coverage gap: 2025-11, 2025-12, 2026-02, 2026-03, 2026-04 are missing – 95598.cn's 执行的电价和收费标准 listing for 黑龙江 "
    "is under maintenance and hl.sgcc.com.cn stops at 2025-08; no official repost found. 2026-01 comes from a 网上国网 资讯 "
    "article (JPG, OCR'd and checked against the image), 2026-05..10 from text PDFs.",
]
