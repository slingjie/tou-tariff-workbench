"""云南电网有限责任公司 代理购电工商业用户电价 — 南方电网 95598 API (official), tables are images → OCR.

Source: 95598.csg.cn information API, areaCode 050000 (see _csg2_util).  Titles
"云南电网有限责任公司YYYY年M月代理购电工商业用户电价表".  The API only holds 2026-06 … 2026-10 (5 notices; earlier
months are not published there – checked with keywords 代理购电 / 电价表 / 工商业用户电价 / 云南电网有限责任公司 / empty).
`link` is either the downloadFiles PDF (2026-06, 2026-08) or a static page whose body is a PDF link (2026-10) or only one
long PNG (2026-07, 2026-09; saved as data/raw/yunnan/YYYY-MM.png, source_url = the PNG).
Inside the PDF the 代理购电工商业用户电价表 is an image (page 1); notes (TOU periods, 1.5倍 rule …) are text on pp. 4-6.

Table (14 ruled columns): 用电分类 | 电压等级 | 代理购电用户电价 | 代理购电价格 (merged) | 上网环节线损电价 | 电度输配电价 |
系统运行费用折合度电水平 ('-') | 政府性基金及附加 | 尖峰 ('-') | 高峰 | 平 | 低谷 | 最大需量 | 变压器容量.
Rows: 两部制工商业用电 不满1千伏 / 1-10千伏 / 35千伏 / 110千伏 / 220千伏(及以上), then 单一制工商业用电 不满1 / 1-10 / 35
(3rd TDP period, to 2026-07) + 110 / 220千伏及以上 (4th period, from 2026-08, 云发改价格〔2026〕602号).
系统运行费用 is printed '-' (it is settled ex post on the next bill, notes 9-11) → stored NULL, so the generic
components check reports components_missing for every Yunnan row (expected; Σ printed components = 代理购电用户电价 holds).

OCR: one tesseract pass over the whole table (digits whitelist, psm 11), words assigned to cells by position; the
leading "0." is often dropped → a bare digit string s is read as int(s)/10**len(s) (all ¥/kWh values < 1).  Every row
is then checked against the notice's own arithmetic (exact to 1e-6): 用户电价 = P + 线损 + 输配 + 基金 = 平段,
峰 = 平 + 50%·P, 谷 = 平 − 50%·P (云发改价格〔2024〕924号 / 〔2026〕51号, note 5).  P (merged cell) = 2·(峰−平) when its own
read disagrees.  需量/容量 cells are re-read cell by cell (3-variant vote) and majority-voted within the month by voltage.
Cache: data/raw/yunnan/ocr_cache/.  All five months were checked by eye against the images (see README).
"""
import json, os, re, subprocess

from . import _csg2_util as U
from . import ocr_table as ot
from . import sgcc_cms as cms
from .. import parse_common as pc

REGION = {"code": "yunnan", "name": "云南", "grid": "csg", "province": "云南省"}
AREA = "050000"
TITLE_RE = re.compile(r"云南电网有限责任公司(\d{4})年(\d{1,2})月代理购电工商业用户电价表")
RAW = os.path.join(pc.DATA, "raw", "yunnan")
_months = cms.months_between
TOU_ID_BASE = 62000
OCR_VERSION = 4


def discover(start, end, log=print):
    wanted = set(_months(start, end))
    by_month = {}
    for it in U.api_list(AREA):
        m = TITLE_RE.search(it["infoTitle"].strip())
        if m:
            month = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
            if month in wanted:
                by_month.setdefault(month, it)
    anns = []
    for month in sorted(by_month):
        it = by_month[month]
        ann = {"month": month, "title": it["infoTitle"].strip(), "publish_date": (it.get("publishTime") or "")[:10],
               "info_id": it.get("infoId"), "class_id": it.get("classId")}
        if "downloadFiles" in it["link"]:
            ann.update(article_url=None, file_url=it["link"], file_ext="pdf")
        else:
            ann["article_url"] = it["link"]
            html = U.G._curl([it["link"]], timeout=40).decode("utf-8", "ignore")
            pdfs = re.findall(r'href="(https://95598\.csg\.cn/ucs/ma/wt/business/downloadFiles\?[^"]+)"', html)
            imgs = re.findall(r'src="(https://95598\.csg\.cn/ucs/sr/info/[^"]+\.(?:png|jpg))"', html)
            if pdfs:
                ann.update(file_url=pdfs[0].replace("&amp;", "&"), file_ext="pdf")
            elif imgs:
                ann.update(file_url=imgs[0], file_ext=imgs[0].rsplit(".", 1)[1])
            else:
                log(f"  !! {month}: neither PDF nor image in {it['link']}")
                continue
        anns.append(ann)
    for m in sorted(wanted - set(by_month)):
        log(f"  {m}: no notice in 95598.csg.cn API (areaCode {AREA}) — not published there")
    return anns


def download(ann, log=print):
    os.makedirs(RAW, exist_ok=True)
    path = os.path.join(RAW, f"{ann['month']}.{ann['file_ext']}")
    if not (os.path.exists(path) and os.path.getsize(path) > 1000):
        data = U.G._curl([ann["file_url"]], timeout=90)
        if ann["file_ext"] == "pdf" and not data.startswith(b"%PDF"):
            raise RuntimeError(f"not a PDF: {ann['file_url']}")
        open(path, "wb").write(data)
        log(f"  downloaded yunnan {ann['month']} ({len(data)} B)")
    ann["file_path"] = os.path.relpath(path, pc.ROOT)
    ann["sha256"] = pc.sha256_file(path)
    return ann


def policy_doc_row(ann):
    return U.policy_doc_row(REGION["code"], ann)


# ------------------------------------------------------------------ OCR
COLS = ["_cat", "_volt", "energy_price", "purchase_price", "line_loss", "tdp", "system_op", "gov_fund", "sharp",
        "peak", "flat", "valley", "demand_price", "capacity_price"]


def _image(path):
    from PIL import Image
    if path.lower().endswith(".pdf"):
        import pypdfium2 as pdfium
        return pdfium.PdfDocument(path)[0].render(scale=3).to_pil().convert("L")
    return Image.open(path).convert("L")


SIX = {"energy_price", "purchase_price", "tdp", "gov_fund", "peak", "flat", "valley"}   # printed with 6 decimals


def _val(s, field=None):
    s = s.strip().strip("-").strip("/")
    if field in SIX:
        digits = s.split(".", 1)[1] if "." in s else (s[1:] if len(s) == 7 and s[:1] == "0" else s)
        if len(digits) != 6 or not digits.isdigit():
            return None                 # a dropped/extra digit (tesseract loses narrow '1's) – not trusted
    if not s or not re.fullmatch(r"\d*\.?\d+\.?", s):
        return None
    if s.endswith("."):
        return None                     # truncated read (e.g. '23.') – not trusted
    if "." in s:
        return float(s if not s.startswith(".") else "0" + s)
    if len(s) == 7 and s[0] == "0":     # '0.453552' read without the dot
        s = s[1:]
    return int(s) / 10 ** len(s)        # '0.' dropped: '00927' = 0.00927, '416397' = 0.416397


def _ocr(ann, log=print):
    import pytesseract
    path = os.path.join(pc.ROOT, ann["file_path"])
    img = _image(path)
    tables, a = ot.find_tables(img, thr=150)
    if not tables:
        raise RuntimeError("no ruled table found")
    t = tables[0]
    xs = ot.vlines(a, t)
    if len(xs) != 15:
        raise RuntimeError(f"expected 14 columns, found {len(xs) - 1}")
    ys = t["hlines"]
    # data rows: rows below the 3 header bands
    rows_y = [(y0, y1) for y0, y1 in zip(ys, ys[1:]) if y1 - y0 > 8]
    hdr = 3 if len(rows_y) in (11, 13) else None
    if hdr is None:
        raise RuntimeError(f"unexpected row count {len(rows_y)}")
    data_y = rows_y[hdr:]
    n_two, n_single = 5, len(data_y) - 5
    crop = img.crop((t["x0"], t["top"], t["x1"], t["bottom"]))
    d = pytesseract.image_to_data(crop, lang="eng", config=f'--tessdata-dir "{ot.TESSDATA}" --psm 11 '
                                  "-c tessedit_char_whitelist=0123456789.-/", output_type=pytesseract.Output.DICT)
    cells = {}
    for i, w in enumerate(d["text"]):
        w = w.strip()
        if not w:
            continue
        cx = d["left"][i] + d["width"][i] / 2 + t["x0"]
        cy = d["top"][i] + d["height"][i] / 2 + t["top"]
        c = next((k for k in range(14) if xs[k] <= cx < xs[k + 1]), None)
        r = next((k for k, (y0, y1) in enumerate(data_y) if y0 <= cy < y1), None)
        if c is not None and r is not None:
            cells.setdefault((r, c), []).append(w)

    def cell(r, c):
        v = cells.get((r, c))
        return _val("".join(v), COLS[c]) if v else None

    def reread(r, c, merged_rows=None):
        y0, y1 = data_y[r] if merged_rows is None else (data_y[merged_rows[0]][0], data_y[merged_rows[1]][1])
        v, raw = ot.ocr_num(img, (xs[c], y0, xs[c + 1], y1), scale=2.0)
        return _val(raw, COLS[c]) if raw else None

    month = ann["month"]
    p4 = month >= "2026-08"
    volts_two = ["不满1千伏", "1-10千伏", "35千伏", "110千伏", "220千伏及以上" if p4 else "220千伏"]
    volts_single = ["不满1千伏", "1-10千伏", "35千伏", "110千伏", "220千伏及以上"][:n_single]
    if n_single not in (3, 5):
        raise RuntimeError(f"unexpected 单一制 row count {n_single}")
    # shared P: merged cell over all rows; take 2·(峰−平) majority, cross-check with direct read
    fixes = []
    rows = []
    for r in range(len(data_y)):
        tt = "两部制" if r < n_two else "单一制"
        volt = volts_two[r] if r < n_two else volts_single[r - n_two]
        row = {"month": month, "category": "工商业用电", "tariff_type": tt, "voltage": volt,
               "voltage_rank": pc.voltage_rank(volt), "system_op": None, "sharp": None, "deep_valley": None}
        for c, f in enumerate(COLS):
            if f.startswith("_") or f in ("system_op", "sharp", "purchase_price"):
                continue
            row[f] = cell(r, c)
        rows.append(row)
    from collections import Counter
    cand = Counter(round(2 * (r["peak"] - r["flat"]), 6) for r in rows if r["peak"] and r["flat"])
    cand.update(round(2 * (r["flat"] - r["valley"]), 6) for r in rows if r["valley"] and r["flat"])
    P_direct = reread(0, 3, merged_rows=(0, len(data_y) - 1))
    P, nP = cand.most_common(1)[0]
    if P_direct is not None and P_direct != P and cand.get(round(P_direct, 6), 0) >= max(2, nP // 2):
        fixes.append(f"P: direct read {P_direct} preferred over {P} ({cand[round(P_direct, 6)]} vs {nP} reads)")
        P, nP = round(P_direct, 6), cand[round(P_direct, 6)]
    if P_direct is not None and abs(P_direct - P) > 1e-9:
        fixes.append(f"P direct read {P_direct} != 2·(峰−平) {P}")
    if nP < len(rows):
        fixes.append(f"P from 2·(峰−平) agreed in {nP}/{2 * len(rows)} reads")
    for r in rows:
        r["purchase_price"] = P
    # E (= 用户电价 = 平段) per row from 4 reads that do not involve 线损/输配; re-read cells when they disagree
    CI = {f: COLS.index(f) for f in ("energy_price", "tdp", "line_loss", "gov_fund", "peak", "flat", "valley")}
    E_of = {}
    for i, r in enumerate(rows):
        def votes(rr):
            c = Counter()
            for f, off in (("energy_price", 0), ("flat", 0), ("peak", P / 2), ("valley", -P / 2)):
                if rr.get(f) is not None:
                    c[round(rr[f] - off, 6)] += 1
            return c
        cands = votes(r)
        if not cands or cands.most_common(1)[0][1] < 3:
            extra = {f: reread(i, CI[f]) for f in ("energy_price", "flat", "peak", "valley")}
            cands = votes(r) + votes(extra)
        E, n = cands.most_common(1)[0]
        if n < 3:
            raise RuntimeError(f"row {i} ({r['tariff_type']} {r['voltage']}): no consistent reading {dict(cands)}")
        E_of[i] = (E, n)
        for f, v in (("energy_price", E), ("flat", E), ("peak", round(E + P / 2, 6)), ("valley", round(E - P / 2, 6))):
            if r.get(f) is None or abs(r[f] - v) > 1e-9:
                fixes.append(f"{r['tariff_type']} {r['voltage']} {f}: OCR {r.get(f)} -> {v} (notice arithmetic, {n} reads agree)")
                r[f] = v
    # 基金: shared, majority
    fund, nf = Counter(r["gov_fund"] for r in rows if r["gov_fund"] is not None).most_common(1)[0]
    # 线损: shared; candidates = direct reads + (E − P − 基金 − 输配_read)
    lc = Counter(r["line_loss"] for r in rows if r["line_loss"] is not None)
    for i, r in enumerate(rows):
        if r.get("tdp") is not None:
            lc[round(E_of[i][0] - P - fund - r["tdp"], 6)] += 1
    loss, nl = lc.most_common(1)[0]
    if nl < len(rows):
        raise RuntimeError(f"线损 not consistent: {dict(lc)}")
    for i, r in enumerate(rows):
        for f, v in (("gov_fund", fund), ("line_loss", loss), ("tdp", round(E_of[i][0] - P - fund - loss, 6))):
            if r.get(f) is None or abs(r[f] - v) > 1e-9:
                fixes.append(f"{r['tariff_type']} {r['voltage']} {f}: OCR {r.get(f)} -> {v}")
                r[f] = v
    # 需量 / 容量: re-read each two-part cell, majority by group
    for r_i, r in enumerate(rows):
        if r["tariff_type"] == "单一制":
            r["demand_price"] = r["capacity_price"] = None
            continue
        for c, f in ((12, "demand_price"), (13, "capacity_price")):
            v = reread(r_i, c)
            if v is not None and (r.get(f) is None or abs(v - r[f]) > 1e-9):
                if r.get(f) is not None:
                    fixes.append(f"{r['voltage']} {f}: whole-table {r[f]} vs cell {v} -> cell")
                r[f] = v
            if r.get(f) is None:
                raise RuntimeError(f"{r['voltage']} {f} unreadable")
    ann["parse_method"] = "OCR (tesseract, whole table + cell re-reads) validated by notice arithmetic"
    ann["ocr_corrections"] = fixes
    return rows


def parse(ann, template=None, log=print):
    return ot.cached_parse(ann, REGION["code"], lambda: _ocr(ann, log), version=OCR_VERSION)


def postprocess(parsed, anns):
    def period(m, rows):
        return "P4" if m >= "2026-08" else "P3"
    return ot.regulated_consensus(parsed, anns, fields=("tdp", "demand_price", "capacity_price"), period_key=period)


def _expected(r):
    P = r["purchase_price"]
    return {"flat": r["energy_price"], "peak": r["energy_price"] + 0.5 * P, "valley": r["energy_price"] - 0.5 * P,
            "energy_price": P + r["line_loss"] + r["tdp"] + r["gov_fund"]}


def extra_validate(rows):
    return U.tou_check(rows, _expected, tol=1.5e-6, label="yn_formula")


# ---------------------------------------------------------------- TOU
TOU_924 = ("https://shidian.gov.cn/info/11678/3666103.htm",
           "云南省发展和改革委员会关于优化调整分时电价政策有关事项的通知（施甸县人民政府转载）", "云发改价格〔2024〕924号",
           "2024-11-18", "2025-01-01", "2026-02-28")
TOU_51 = ("https://95598.csg.cn/ucs/ma/wt/business/downloadFiles?documentId=bmZkd195eF90eWZ3L3Nkay9vc3NGaWxlLzZhNmM2NzhhZjYyNzQ3MzZhNWQwYjc0NWZkYzBiNTk3L1BERg==&documentType=pdf&documentName=downloadFile",
          "云南省发展和改革委员会关于进一步优化调整分时电价政策的通知（南方电网95598发布）", "云发改价格〔2026〕51号",
          "2026-01-19", "2026-03-01", None)
SPEC = {"peak": [(7, 9), (18, 24)], "flat": [(0, 2), (6, 7), (9, 12), (16, 18)], "valley": [(2, 6), (12, 16)]}


def tou_rules():
    code = REGION["code"]
    out = []
    for i, doc_t in enumerate((TOU_924, TOU_51)):
        d = cms.tou_doc(code, *doc_t)
        rules = [(list(range(1, 13)), "normal", cms.segs_from_hours(SPEC), doc_t[4], doc_t[5])]
        out.append((d, cms.tou_rows(code, d, rules, TOU_ID_BASE + 10 * i)))
    return out


TOU_NOTES = [
    "Source: 南方电网 95598 information API (areaCode 050000). Only 2026-06..2026-10 are published there; 2025-11..2026-05 "
    "missing (no official copy reachable: yn.csg.cn unreachable, 楚雄州发改委 repost list returns 403).",
    "Table is an image → OCR, validated row by row against the notice arithmetic (用户电价 = P+线损+输配+基金 = 平; 峰/谷 = 平 ± 50%·P). "
    "系统运行费用 printed '-' (settled ex post) → NULL; 尖峰 '-' (suspended) → NULL; no 深谷.",
    "TOU: 云发改价格〔2024〕924号 (2025-01-01 .. 2026-02-28) and 云发改价格〔2026〕51号 (from 2026-03-01, official PDF on 95598.csg.cn): "
    "identical periods 峰 7-9、18-24; 平 0-2、6-7、9-12、16-18; 谷 2-6、12-16; ratio 1.5:1:0.5 on 代理购电价格 only; 尖峰暂缓. "
    "From 2026-03-01 regulated TOU applies only to agency 大工业 and ≥100 kVA 一般工商业 users; <100 kVA 单一制 users pay the 平段 "
    "price (note 6 of each notice).",
]
