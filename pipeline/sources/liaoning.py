"""国网辽宁省电力有限公司 代理购电工商业用户电价 — thin config over sgcc_cms + OCR (ocr_table, read-only use).

Quirks
* Own list http://www.ln.sgcc.com.cn/html/main/col3021/column_3021_1.html ("代理购电价格（YYYY年M月）") is
  unreachable from the pipeline network and the Hark browser.  The same PDFs are on State Grid's 网上国网 portal
  95598.cn › 信息公开 › 执行的电价和收费标准 (region 辽宁); omg-static URLs collected by the browser into
  data/raw/liaoning/seed.json (curl downloads them).
* The PDFs are SCANS (page 1 = 代理购电工商业用户电价表 image, page 2 = 价格信息表 image) -> OCR of page 1 with
  ocr_table.find_tables/read_table, then ocr_table.solve_rows cross-validates every cell against the table's own
  relations: 非分时 = 代理购电 + 线损 + 输配 + 基金 + 系统运行 = 平时段; 高峰/低谷 = 平 ± 50%·P; 尖峰 = 高峰 + 25%·1.5P
  (P = 代理购电价格, printed in note / 辽宁 2023 分时 policy) — a cell is repaired only when ≥ 2 independent cells agree.
  需量/容量 are regulated: OCR reads are replaced/filled by the value most months of the same TDP period agree on.
* Layout: 3rd TDP period (to 2026-07) 6 rows: 单一制 不满1千伏 / 1~10（20）千伏 / 110（66）千伏; 两部制 1~10（20）千伏 /
  110（66）千伏 / 220千伏及以上.  4th period (2026-08 on, 发改价格〔2026〕1077号) 8 rows: 单一制 + 两部制 each
  不满1 / 1~10（20） / 110（66） / 220及以上.  尖峰 printed only in some months (blank otherwise -> NULL); no 深谷.
"""
import re
from collections import Counter

from . import sgcc_cms as cms
from . import ocr_table as ot
from .. import parse_common as pc

REGION = {"code": "liaoning", "name": "辽宁", "grid": "sgcc", "province": "辽宁省"}
CMS = cms.CMSRegion(REGION, "http://www.ln.sgcc.com.cn", "3021",
                    title_res=[r"代理购电价格[（(](\d{4})年(\d{1,2})月[)）]"], max_pages=2, timeout=15,
                    live_list=False, file_pref=["pdf"])

_months = cms.months_between
discover = CMS.discover
download = CMS.download
policy_doc_row = CMS.policy_doc_row
TOU_ID_BASE = 11000
OCR_VERSION = 4

COLUMNS = ["energy_price", "purchase_price", "line_loss", "tdp", "gov_fund", "system_op",
           "sharp", "peak", "flat", "valley", "demand_price", "capacity_price"]
V1, V2, V110, V220 = "不满1千伏", "1~10（20）千伏", "110（66）千伏", "220千伏及以上"
LAYOUT = {6: [("单一制", V1), ("单一制", V2), ("单一制", V110), ("两部制", V2), ("两部制", V110), ("两部制", V220)],
          8: [("单一制", V1), ("单一制", V2), ("单一制", V110), ("单一制", V220),
              ("两部制", V1), ("两部制", V2), ("两部制", V110), ("两部制", V220)]}
COEF = {"peak": 0.5, "valley": 0.5, "sharp_on_peak": 0.25}


def _page_image(path, page=0):
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(path)
    imgs = [o for o in doc[page].get_objects() if o.type == 3]
    best = max((o.get_bitmap(render=False).to_pil() for o in imgs), key=lambda b: b.width * b.height)
    return best.convert("L")


def _ocr(ann, log):
    img = _page_image(ann["file_path"], 0)
    head = re.sub(r"\s+", "", ot._tess(img.crop((0, int(img.height * 0.04), img.width, int(img.height * 0.22))),
                                       "chi_sim", psm=6))
    m = re.search(r"(\d{4})年(\d{1,2})月1日", head)
    if m and f"{int(m.group(1)):04d}-{int(m.group(2)):02d}" != ann["month"]:
        raise RuntimeError(f"执行时间 OCR says {m.group(0)}, expected {ann['month']}")
    ann["ocr_title"] = head[:80]
    tables, a = ot.find_tables(img)
    t = max(tables, key=lambda t: t["bottom"] - t["top"])
    t["x0"], t["x1"] = max(t["x0"], 3), min(t["x1"], img.width - 4)
    scale = 1.3 if img.width >= 2500 else 2.0
    tab = ot.read_table(img, t, a, scale=scale, log=log)
    n = len(tab["data"])
    if n not in LAYOUT:
        raise RuntimeError(f"OCR: {n} data rows, expected 6 or 8")
    pre = _reread_regulated(img, tab, LAYOUT[n])
    pre += _preclean(tab)
    rows, fixes = ot.solve_rows(tab, COLUMNS, LAYOUT[n], ann["month"], COEF, base_kind="month", tol=2.5e-6,
                                log=log)
    fixes = pre + fixes
    for r in rows:   # sanity: a repair must never produce an impossible value
        for f in ("energy_price", "purchase_price", "tdp", "peak", "flat", "valley", "sharp"):
            v = r.get(f)
            if v is not None and not (0 < v < 3):
                raise RuntimeError(f"OCR: implausible {f}={v} for {r['tariff_type']} {r['voltage']}")
        for f in ("line_loss", "gov_fund", "system_op"):
            v = r.get(f)
            if v is None or not (-0.3 < v < 0.5):
                raise RuntimeError(f"OCR: implausible {f}={v}")
    for r in rows:
        if r["tariff_type"] == "单一制":
            r["demand_price"] = r["capacity_price"] = None
        r["category"] = "工商业用电"
    ann["ocr_corrections"] = fixes
    ann["parse_method"] = f"OCR (tesseract) of scanned PDF page 1 + formula cross-check ({len(fixes)} repairs)"
    ann["effective_from"], ann["effective_to"] = cms.effective_dates("", ann["month"])
    return rows


def _reread_regulated(img, tab, layout):
    """Short integers (需量/容量 like '24', '22') are often dropped by the line OCR: re-read blank 两部制 cells one by
    one at 3x with a digit whitelist."""
    fixes = []
    cols, bands = tab["cols"], tab["rows"]
    for k, ((nums, raws), (tt, v)) in enumerate(zip(tab["data"], layout)):
        if tt != "两部制":
            continue
        nums, raws = list(nums), list(raws)
        for c in (10, 11):
            if nums[c] is not None or c >= len(cols):
                continue
            (x0, x1), (y0, y1) = cols[c], bands[k]
            cell = img.crop((x0 + 4, y0 + 4, x1 - 4, y1 - 4))
            cell = cell.resize((cell.width * 3, cell.height * 3))
            txt = ot._tess(cell, "eng", psm=7, extra="-c tessedit_char_whitelist=0123456789.")
            if re.fullmatch(r"\d{2}(\.\d)?", txt or ""):
                nums[c], raws[c] = float(txt), txt
                fixes.append(f"{tt} {v} {COLUMNS[c]}: blank in line OCR, cell re-read '{txt}'")
        tab["data"][k] = (nums, raws)
    return fixes


def _preclean(tab):
    """Price cells are 0.xxxxxx: a stray digit picked up from a rule or bracket ('10.026825', '9.543828') or a
    trailing dot is dropped before the formula cross-check (logged)."""
    fixes = []
    for k, (nums, raws) in enumerate(tab["data"]):
        nums, raws = list(nums), list(raws)
        for c, (v, raw) in enumerate(zip(nums, raws)):
            if c >= 10 or not raw:          # 需量/容量 handled by postprocess
                continue
            s = raw.rstrip(".")
            m = re.fullmatch(r"\d(0\.\d{4,})", s)
            if m and (v is None or v >= 1):
                fixes.append(f"row{k} {COLUMNS[c]}: OCR '{raw}' -> {m.group(1)} (stray leading digit)")
                nums[c], raws[c] = float(m.group(1)), m.group(1)
            elif re.fullmatch(r"[2-9]\.\d{6}", s) and COLUMNS[c] in ("energy_price", "flat", "valley"):
                # these columns are always < 1 元: a leading '0' misread as another digit
                fixes.append(f"row{k} {COLUMNS[c]}: OCR '{raw}' -> 0{s[1:]} (leading 0 misread)")
                nums[c], raws[c] = float("0" + s[1:]), "0" + s[1:]
            elif s != raw and re.fullmatch(r"-?\d+\.\d+", s):
                nums[c], raws[c] = float(s), s
        tab["data"][k] = (nums, raws)
    return fixes


FIX_RE = re.compile(r"^(单一制|两部制) (\S+) (\w+): OCR '([\d.\-]*)' -> (-?\d+\.\d+) \(\d+ cells agree")


def _snap_to_print(rows, fixes):
    """A formula repair is exact only to the 7th decimal (prices are printed rounded to 6).  When the OCR string
    differs from the repaired value in one character, the printed number is that string with the one bad digit
    replaced: pick the 6-decimal string closest to the derived value among single-character edits of the read."""
    out = []
    for fx in fixes:
        m = FIX_RE.match(fx)
        if not m:
            continue
        tt, v, f, raw, new = m.groups()
        val = float(new)
        if len(raw) != 8 or not raw.startswith("0."):
            continue
        cands = []
        for i in range(2, 8):
            for d in "0123456789":
                s = raw[:i] + d + raw[i + 1:]
                if abs(float(s) - val) <= 1.5e-6:
                    cands.append(s)
        if len(cands) == 1:
            for r in rows:
                if r["tariff_type"] == tt and r["voltage"] == v and abs((r.get(f) or 0) - val) < 1e-9:
                    r[f] = float(cands[0])
                    out.append(f"{tt} {v} {f}: {new} -> {cands[0]} (one-digit OCR error, printed value)")
    return out


def parse(ann, template=None, log=print):
    rows = ot.cached_parse(ann, REGION["code"], lambda: _ocr(ann, log), version=OCR_VERSION)
    snaps = _snap_to_print(rows, ann.get("ocr_corrections") or [])
    if snaps:
        ann["ocr_corrections"] = list(ann.get("ocr_corrections") or []) + snaps
    return rows


def postprocess(parsed, anns):
    """需量/容量 are regulated within a TDP period (row count identifies the period): fill / replace OCR reads by the
    value the majority of the period's months read."""
    log = []
    groups = {}
    for m, rows in parsed.items():
        for r in rows:
            if r["tariff_type"] != "两部制":
                continue
            for f in ("demand_price", "capacity_price"):
                groups.setdefault((len(rows), r["voltage"], f), []).append((m, r))
    for (n, v, f), items in groups.items():
        cnt = Counter(r[f] for _, r in items if r.get(f) is not None)
        if not cnt:
            continue
        val, k = cnt.most_common(1)[0]
        if k < 2 or k <= sum(cnt.values()) / 2:
            continue
        for m, r in items:
            if r.get(f) != val:
                msg = f"两部制 {v} {f}: OCR {r.get(f)} -> {val} (regulated, {k} months of the period agree)"
                anns[m].setdefault("ocr_corrections", []).append(msg)
                log.append(f"{m} {msg}")
                r[f] = val
    return log


# ---------------------------------------------------------------- TOU (辽宁省发改委 2023-08-25, eff 2023-09-01)
TOU_SRC = ("https://fgw.ln.gov.cn/fgw/index/tzgg/2023083014061818174/index.shtml",
           "省发展改革委关于进一步完善分时电价机制有关事项的通知", "辽发改价格〔2023〕441号", "2023-08-25", "2023-09-01")
# 文号 from 辽宁省发改委《现行有效的行政规范性文件目录（2024年版）》 item 25
# (https://fgw.ln.gov.cn/fgw/zc/zxzc/2024061116153017150/index.shtml). Checked 2026-10-11: no newer 工商业 TOU document;
# 2025 documents are 居民电采暖 (辽发改价格〔2025〕898号) and 煤改电 only, and every 2025-11..2026-10 table still applies ±50%/+25%.


def tou_rules():
    code = REGION["code"]
    doc = cms.tou_doc(code, *TOU_SRC)
    base = {"peak": [(7.5, 10.5), (16, 21)], "valley": [(11.5, 12.5), (22, 5)]}
    rules = [([1, 7, 8, 12], "normal", cms.segs_from_hours(dict(base, sharp=[(17, 19)]))),
             ([2, 3, 4, 5, 6, 9, 10, 11], "normal", cms.segs_from_hours(base))]
    return doc, cms.tou_rows(code, doc, rules, TOU_ID_BASE)


TOU_NOTES = [
    "Files: State Grid 95598.cn static copies (网上国网 › 执行的电价和收费标准, region 辽宁) of the monthly table; the PDFs are scans -> OCR, "
    "every value cross-checked by the table's own sum and TOU relations (repairs logged in announcements.json ocr_corrections).",
    "TOU periods per 辽宁省发改委《关于进一步完善分时电价机制有关事项的通知》辽发改价格〔2023〕441号 (2023-08-25, eff 2023-09-01; still the latest 工商业 TOU document as of 2026-10): "
    "峰 7:30-10:30, 16-21; 谷 11:30-12:30, 22-5; others 平; 尖峰 17-19 in 7、8、12、1月. 1.875:1.5:1:0.5 on the 代理购电价格 (table: 峰/谷 = 平 ± 50%·P).",
    "Since 2025-03-01 Liaoning runs a continuous spot market: the agency-purchase price actually settles at market prices (note 2 of every table); "
    "the printed TOU prices are the published reference. 尖峰 stored only where printed (NULL when the column is blank); 深谷 not published.",
]


def extra_validate(rows):
    X = {}
    for r in rows:
        X.setdefault(r["month"], r["purchase_price"])

    def exp(r):
        P, f = X[r["month"]], r["flat"]
        e = {"peak": f + 0.5 * P, "valley": f - 0.5 * P, "flat": r["energy_price"]}
        if r.get("sharp") is not None:
            e["sharp"] = f + 0.875 * P
        return e
    return cms.tou_formula_check(rows, exp, tol=0.000002)
