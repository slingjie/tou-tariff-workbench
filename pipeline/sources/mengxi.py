"""内蒙古电力（集团）有限责任公司（蒙西电网）代理购电工商业用户电价 — official GOVERNMENT repost + OCR.

Quirks
* Not SGCC/CSG: 蒙西 is served by 内蒙古电力（集团）. Its own site impc.com.cn (articles
  /content/YYYY-MM/DD/content_N.html) times out from the pipeline network.  Every month the notice is reposted by
  正镶白旗人民政府 (锡林郭勒盟) › 政府信息公开 › 法定主动公开内容 › 通知公告, source 正镶白旗供电分公司:
    list     https://www.zxbq.gov.cn/eportal/ui?pageId=6cffcf6c91144e8a83ef0649e7e45da2&currentPage=N&moduleId=c92754c6ad4248a396dce21019855d4d&staticRequest=yes
    article  https://www.zxbq.gov.cn/zxbq/zwgk/zfxxgk/fdzdgknr/tzgg/<ts>/index.html
  title "内蒙古电力（集团）有限责任公司代理购电工商业用户电价表（执行时间：YYYY年M月）".  The article body is 3–4 images
  (公告 text, 代理购电工商业用户电价表, 代理购电价格表, from 2026-06 also the 1.5倍 table), in no fixed order and
  sometimes the same table twice at two resolutions (2026-07).  The main table image is chosen by OCR'ing each
  image's title (contains 代理购电工商业用户电价表, not 1.5倍; largest image wins) and saved as
  data/raw/mengxi/YYYY-MM.<ext>; all images are kept in data/raw/mengxi/img/.
* Table: columns 电度用电价格 | 代理购电价格 线损 输配 系统运行 基金 | [尖峰] 高峰 平 低谷 [深谷] | 需量 容量.
  尖峰/深谷 columns are printed in 6-8月 (小风季) only.  Layout: to 2026-07 单一制 3 rows (不满1 / 1~10（20）/ 35千伏) +
  两部制 4 (1~10（20）/ 35 / 110 / 220千伏及以上); from 2026-08 (4th TDP period) 单一制 5 + 两部制 5.
* OCR cross-validation with the notice's own formula row (内发改价费字〔2023〕1630号):  电度 = Σ components = 平段;
  大风季(1-5、9-12月) 峰 = 平 + 68%·P, 谷 = 平 − 52%·P;  小风季(6-8月) 峰 = 平 + 54%·P, 谷 = 平 − 56%·P,
  尖峰 = (P·1.54)·1.2 + rest = 平 + 84.8%·P, 深谷 = (P·0.44)·0.8 + rest = 平 − 64.8%·P  (P = 代理购电价格, one value per
  month → ocr_table base_kind='month').  Regulated 输配/需量/容量 by majority within a TDP period.
"""
import json, os, re, shutil

from . import sgcc_cms as cms
from . import ocr_table as ot
from .. import parse_common as pc

REGION = {"code": "mengxi", "name": "蒙西", "grid": "impc", "province": "内蒙古自治区"}
HOST = "https://www.zxbq.gov.cn"
LIST = HOST + "/eportal/ui?pageId=6cffcf6c91144e8a83ef0649e7e45da2&currentPage={p}&moduleId=c92754c6ad4248a396dce21019855d4d&staticRequest=yes"
RAW = os.path.join(pc.DATA, "raw", "mengxi")
TITLE = re.compile(r"内蒙古电力（集团）有限责任公司代理购电工商业用户电价表（执行时间：(\d{4})年(\d{1,2})月）")
LINK = re.compile(r'href="(/zxbq/zwgk/zfxxgk/fdzdgknr/tzgg/\d+/index\.html)"[^>]*?\stitle="([^"]+)"')
IMG = re.compile(r'src="(/zxbq/zwgk/zfxxgk/fdzdgknr/tzgg/\d+/\d+\.(?:png|jpe?g))"', re.I)
_months = cms.months_between
TOU_ID_BASE = 65000
OCR_VERSION = 1
P4_FROM = "2026-08"

COLS_BASE = ["energy_price", "purchase_price", "line_loss", "tdp", "system_op", "gov_fund"]
V3 = ["不满1千伏", "1~10（20）千伏", "35千伏"]
V5 = ["不满1千伏", "1~10（20）千伏", "35千伏", "110千伏", "220千伏及以上"]
LAYOUT_P3 = [("单一制", v) for v in V3] + [("两部制", v) for v in V5[1:]]
LAYOUT_P4 = [("单一制", v) for v in V5] + [("两部制", v) for v in V5]


def _summer(month):
    return int(month[5:]) in (6, 7, 8)


def _coef(month):
    if _summer(month):
        return {"peak": 0.54, "valley": 0.56, "sharp_on_peak": 0.20, "deep_on_valley": 0.20}
    return {"peak": 0.68, "valley": 0.52}


# ---------------------------------------------------------------- discover / download
def discover(start, end, log=print):
    wanted = set(_months(start, end))
    out = {}
    for p in range(1, 21):
        html = pc.http_get(LIST.format(p=p), timeout=30).content.decode("utf-8", "ignore")
        for path, title in LINK.findall(html):
            m = TITLE.search(title)
            if not m:
                continue
            key = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
            if key in wanted and key not in out:
                out[key] = {"month": key, "title": title, "article_url": HOST + path, "file_url": None,
                            "file_ext": None, "publish_date": None, "discovered_via": "gov_repost:zxbq.gov.cn"}
                log(f"  {key}: {HOST + path}")
        if wanted <= set(out) or not LINK.findall(html):
            break
    return [out[k] for k in sorted(out)]


def _title_of(path):
    img = ot.load_image(path)
    w, h = img.size
    top = img.crop((0, int(h * 0.08), w, int(h * 0.25)))
    if top.width > 2000:
        top = top.resize((top.width // 2, top.height // 2))
    return re.sub(r"\s+", "", ot._tess(top, "chi_sim", psm=6)), w * h


def download(ann, log=print):
    os.makedirs(os.path.join(RAW, "img"), exist_ok=True)
    os.makedirs(os.path.join(RAW, "html"), exist_ok=True)
    hp = os.path.join(RAW, "html", f"{ann['month']}.html")
    if not (os.path.exists(hp) and os.path.getsize(hp) > 5000):
        open(hp, "wb").write(pc.http_get(ann["article_url"], timeout=40).content)
    html = open(hp, encoding="utf-8", errors="ignore").read()
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s+\d{2}:\d{2}\s*文章来源", re.sub(r"<[^>]+>", " ", html))
    ann["publish_date"] = m.group(1) if m else ann.get("publish_date")
    sel_path = os.path.join(RAW, "selection.json")
    sel = json.load(open(sel_path, encoding="utf-8")) if os.path.exists(sel_path) else {}
    imgs = []
    for k, src in enumerate(dict.fromkeys(IMG.findall(html)), 1):
        ext = src.rsplit(".", 1)[1].lower()
        p = os.path.join(RAW, "img", f"{ann['month']}-{k}.{ext}")
        if not _image_ok(p):
            open(p, "wb").write(pc.http_get(HOST + src, timeout=90).content)
            if not _image_ok(p):
                raise RuntimeError(f"truncated image {HOST + src}")
        imgs.append((HOST + src, p))
    ann["img_urls"] = [u for u, _ in imgs]
    if ann["month"] not in sel or sel[ann["month"]]["url"] not in ann["img_urls"]:
        cands = []
        for u, p in imgs:
            title, area = _title_of(p)
            if "代理购电工商业用户电价表" in title and "1.5" not in title:
                cands.append((area, u, p, title))
        if not cands:
            raise RuntimeError("main tariff image not identified by OCR title")
        area, u, p, title = max(cands)
        sel[ann["month"]] = {"url": u, "title_ocr": title[:80]}
        json.dump(sel, open(sel_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True)
    u = sel[ann["month"]]["url"]
    p = dict(imgs)[u]
    ext = p.rsplit(".", 1)[1]
    dst = os.path.join(RAW, f"{ann['month']}.{ext}")
    if not (os.path.exists(dst) and pc.sha256_file(dst) == pc.sha256_file(p)):
        shutil.copyfile(p, dst)
    ann.update(file_url=u, file_ext=ext, file_path=dst, sha256=pc.sha256_file(dst))
    return dst


def _image_ok(p):
    if not os.path.exists(p) or os.path.getsize(p) < 10000:
        return False
    try:
        from PIL import Image
        Image.open(p).load()
        return True
    except Exception:
        return False


def policy_doc_row(ann):
    return {"id": pc.doc_id(ann["file_url"]), "region_code": REGION["code"], "kind": "agency_price",
            "title": ann["title"] + "（正镶白旗人民政府转载，正镶白旗供电分公司）", "doc_no": None,
            "publish_date": ann.get("publish_date"),
            "effective_from": ann.get("effective_from") or f"{ann['month']}-01", "effective_to": ann.get("effective_to"),
            "source_url": ann["file_url"], "file_path": os.path.relpath(ann["file_path"], pc.ROOT),
            "sha256": ann["sha256"], "fetched_at": pc.now_iso()}


# ---------------------------------------------------------------- OCR
def _exact(r):
    P, f = r["purchase_price"], r["energy_price"]
    c = _coef(r["month"])
    e = {"flat": f, "peak": round(f + c["peak"] * P, 6), "valley": round(f - c["valley"] * P, 6)}
    if r.get("sharp") is not None:
        e["sharp"] = round(f + (1.54 * 1.2 - 1) * P, 6)
    if r.get("deep_valley") is not None:
        e["deep_valley"] = round(f + (0.44 * 0.8 - 1) * P, 6)
    return e


def _ocr(ann, log):
    img = ot.load_image(ann["file_path"])
    tables, a = ot.find_tables(img, thr=150)
    if not tables:
        raise RuntimeError("no ruled table found")
    t = max(tables, key=lambda x: len(x["hlines"]))
    h = int(img.height * 0.08)
    head = re.sub(r"\s+", "", ot._tess(img.crop((t["x0"], max(0, t["top"] - h), t["x1"], t["top"] - 2)), "chi_sim", psm=6))
    ann["ocr_title"] = head[:80]
    m = re.search(r"(\d{4})年(\d{1,2})月", head)
    if m and f"{int(m.group(1)):04d}-{int(m.group(2)):02d}" != ann["month"]:
        raise RuntimeError(f"执行时间 OCR says {m.group(0)}, expected {ann['month']}")
    scale = 1.0 if img.width >= 3000 else (1.5 if img.width >= 2000 else 2.5)
    alt = 1.5 if scale == 1.0 else (2.0 if scale == 1.5 else 3.0)
    try:
        return _ocr_at(ann, img, t, a, head, scale, log)
    except RuntimeError as e:
        log(f"  OCR {ann['month']} at ×{scale} failed ({e}); retrying at ×{alt}")
        return _ocr_at(ann, img, t, a, head, alt, log)


def _fix_purchase(tab, month, fixes):
    """P = 代理购电价格 is the only floated component: every row's (峰−平)/k_peak and (平−谷)/k_valley equals P.  When ≥ 3
    rows agree on that value and the merged P cell was misread, use it (P printed with 4 decimals)."""
    c = _coef(month)
    ipk, ifl, iva, ip = 6 + (1 if _summer(month) else 0), 7 + (1 if _summer(month) else 0), 8 + (1 if _summer(month) else 0), 1
    cands = []
    for nums, raws in tab["data"]:
        try:
            cands.append(round((nums[ipk] - nums[ifl]) / c["peak"], 4))
            cands.append(round((nums[ifl] - nums[iva]) / c["valley"], 4))
        except (TypeError, IndexError):
            pass
    if not cands:
        return
    best = max(set(cands), key=cands.count)
    if cands.count(best) < 3:
        return
    for k, (nums, raws) in enumerate(tab["data"]):
        if nums[ip] is None or abs(nums[ip] - best) > 5e-5:
            fixes.append(f"row {k + 1} purchase_price: OCR '{raws[ip]}' -> {best:.4f} ((峰−平)/{c['peak']} and (平−谷)/{c['valley']}, {cands.count(best)} cells agree)")
            nums[ip] = best
            raws[ip] = f"{best:.4f}"
    # with P settled, the merged 系统运行费 cell is checked against Σ = 电度 − 输配 (mode over rows); LL/基金 are fixed
    # regulated-looking values, so only 系统运行费 is repaired here (when the OCR string is within 2 edits)
    S = [round(n[0] - n[3], 6) for n, _ in tab["data"] if n[0] is not None and n[3] is not None]
    if not S:
        return
    Sm = max(set(S), key=S.count)
    if S.count(Sm) < 3:
        return
    n0, r0 = tab["data"][0]
    if None in (n0[1], n0[2], n0[5]):
        return
    cand = round(Sm - n0[1] - n0[2] - n0[5], 6)
    if n0[4] is None or abs(n0[4] - cand) > 2e-6:
        raw = r0[4] or ""
        nd = len(raw.split(".")[1]) if "." in raw else 4
        new_s = f"{cand:.{nd}f}"
        if abs(float(new_s) - cand) < 1e-9 and ot.edit_distance(raw, new_s) <= 2:
            for k, (nums, raws) in enumerate(tab["data"]):
                nums[4] = cand
                raws[4] = new_s
            fixes.append(f"system_op: OCR '{raw}' -> {new_s} (电度 − 输配 − P − 线损 − 基金, {S.count(Sm)} rows agree)")


def _ocr_at(ann, img, t, a, head, scale, log):
    tab = ot.read_table(img, t, a, scale=scale, log=log)
    # the 小风季 公式 row contains '1.2' / '0.8' (decimals) and is mistaken for data: keep rows with ≥ 5 decimals
    tab["data"] = [(n, r) for n, r in tab["data"] if sum(1 for x in r if x and "." in x) >= 5]
    nrow = len(tab["data"])
    layout = LAYOUT_P4 if nrow == 10 else LAYOUT_P3 if nrow == 7 else None
    if layout is None:
        raise RuntimeError(f"OCR: {nrow} data rows (expected 7 or 10)")
    if (layout is LAYOUT_P4) != (ann["month"] >= P4_FROM):
        raise RuntimeError(f"OCR: {nrow} rows does not match the TDP period of {ann['month']}")
    ncol = len(tab["data"][0][0])
    if ncol == 13:
        columns = COLS_BASE + ["sharp", "peak", "flat", "valley", "deep_valley", "demand_price", "capacity_price"]
    elif ncol == 11:
        columns = COLS_BASE + ["peak", "flat", "valley", "demand_price", "capacity_price"]
    else:
        raise RuntimeError(f"OCR: {ncol} numeric columns (expected 11 or 13)")
    if (ncol == 13) != _summer(ann["month"]):
        raise RuntimeError(f"OCR: {ncol} columns but month {ann['month']} {'is' if _summer(ann['month']) else 'is not'} 小风季")
    pre = []
    _fix_purchase(tab, ann["month"], pre)
    rows, fixes = ot.solve_rows(tab, columns, layout, ann["month"], _coef(ann["month"]), base_kind="month",
                                tol=1.2e-6, log=log)
    fixes = pre + fixes
    for r in rows:
        r["category"] = "工商业用电"
        r.setdefault("sharp", None)
        r.setdefault("deep_valley", None)
        if r["tariff_type"] == "单一制":
            r["demand_price"] = r["capacity_price"] = None
        try:
            exp = _exact(r)
        except TypeError:
            continue
        for f, v in exp.items():
            if r.get(f) is not None and r[f] != v and abs(r[f] - v) <= 1.2e-6:
                fixes.append(f"{r['tariff_type']} {r['voltage']} {f}: {r[f]} -> {v} (snap to printed arithmetic)")
                r[f] = v
    for r in rows:
        for f in ("energy_price", "purchase_price", "tdp", "peak", "flat", "valley"):
            v = r.get(f)
            if v is None or not (0 < v < 3):
                raise RuntimeError(f"OCR: implausible {f}={v} for {r['tariff_type']} {r['voltage']}")
    bad = [x for x in pc.validate_rows(rows) + extra_validate(rows)
           if x["check"] in ("components_sum", "components_missing", "tou_formula", "tou_order")]
    if bad:
        raise RuntimeError(f"OCR unreliable: {len(bad)} formula failures, e.g. {bad[0]['check']} {bad[0]['voltage']} {bad[0]['detail']}")
    ann["ocr_corrections"] = fixes
    ann["ocr_voltage_digits"] = tab["voltage_digits"]
    ann["parse_method"] = f"OCR (tesseract ×{scale}) of gov-repost image + formula cross-check ({len(fixes)} repairs)"
    ann["effective_from"], ann["effective_to"] = cms.effective_dates("", ann["month"])
    return rows


def parse(ann, template=None, log=print):
    rows = ot.cached_parse(ann, REGION["code"], lambda: _ocr(ann, log), version=OCR_VERSION)
    if any(r["tariff_type"] == "两部制" and (r.get("demand_price") is None or r.get("capacity_price") is None)
           for r in rows):
        fx = _reread_blank(ann, rows)
        if fx:
            ann["ocr_corrections"] = list(ann.get("ocr_corrections") or []) + fx
    return rows


def _reread_blank(ann, rows):
    """Faint regulated numbers under the watermark are sometimes missed: re-read blank 两部制 需量/容量 cells one by one."""
    img = ot.load_image(ann["file_path"])
    tables, a = ot.find_tables(img, thr=150)
    if not tables:
        return []
    t = max(tables, key=lambda x: len(x["hlines"]))
    xs = ot.vlines(a, t)
    cols = [(x0, x1) for x0, x1 in zip(xs, xs[1:]) if x1 - x0 > 8]
    bands = [(y0, y1) for y0, y1 in zip(t["hlines"], t["hlines"][1:]) if y1 - y0 > 8][-len(rows):]
    if len(bands) != len(rows) or len(cols) < 2:
        return []
    fixes = []
    for k, r in enumerate(rows):
        if r["tariff_type"] != "两部制":
            continue
        for f, (x0, x1) in (("demand_price", cols[-2]), ("capacity_price", cols[-1])):
            if r.get(f) is not None:
                continue
            y0, y1 = bands[k]
            for thr in (200, 170, None):
                cell = img.crop((x0 + 4, y0 + 4, x1 - 4, y1 - 4))
                cell = cell.resize((cell.width * 3, cell.height * 3))
                if thr:
                    cell = cell.point(lambda p, thr=thr: 0 if p < thr else 255)
                txt = ot._tess(cell, "eng", psm=7, extra="-c tessedit_char_whitelist=0123456789.")
                if re.fullmatch(r"\d{2}\.\d", txt or ""):
                    r[f] = float(txt)
                    fixes.append(f"两部制 {r['voltage']} {f}: blank in line OCR, cell re-read '{txt}'")
                    break
    return fixes


def _period(m, rows=None):
    return "P4" if m >= P4_FROM else "P3"


def postprocess(parsed, anns):
    """Regulated prices: majority vote within a TDP period, then fill still-blank 两部制 需量/容量 from the majority."""
    from collections import Counter
    log = ot.regulated_consensus(parsed, anns, fields=("tdp", "demand_price", "capacity_price"), period_key=_period)
    groups = {}
    for m, rows in parsed.items():
        for r in rows:
            if r["tariff_type"] == "两部制":
                for f in ("demand_price", "capacity_price"):
                    groups.setdefault((_period(m), r["voltage"], f), []).append((m, r))
    for (pk, v, f), items in groups.items():
        cnt = Counter(r[f] for _, r in items if r.get(f) is not None)
        if not cnt:
            continue
        val, n = cnt.most_common(1)[0]
        if n < 2 or n <= sum(cnt.values()) / 2:
            continue
        for m, r in items:
            if r.get(f) is None:
                msg = f"两部制 {v} {f}: blank in OCR -> {val} (regulated, {n} months of {pk} agree)"
                anns[m].setdefault("ocr_corrections", []).append(msg)
                log.append(f"{m} {msg}")
                r[f] = val
    return log


def extra_validate(rows):
    fails = cms.tou_formula_check(rows, _exact, tol=1.5e-6)
    for m in sorted({r["month"] for r in rows}):
        for tt in ("单一制", "两部制"):
            g = sorted([r for r in rows if r["month"] == m and r["tariff_type"] == tt], key=lambda r: r["voltage_rank"])
            for lo, hi in zip(g, g[1:]):
                if not hi["tdp"] < lo["tdp"]:
                    fails.append({"month": m, "category": hi["category"], "tariff_type": tt, "voltage": hi["voltage"],
                                  "check": "tdp_order", "detail": f"tdp {hi['tdp']} !< {lo['tdp']}"})
    return fails


# ---------------------------------------------------------------- TOU 内发改价费字〔2023〕1630号
TOU_1630 = ("https://fgw.xlgl.gov.cn/fgw/ywgz/zdxm/tzgg/2024010409440178739/index.html",
            "内蒙古自治区发展和改革委员会关于完善蒙西电网工商业分时电价政策的通知（试行）（锡林郭勒盟发改委转载）",
            "内发改价费字〔2023〕1630号", "2023-12-28", "2024-02-01")


def tou_rules():
    code = REGION["code"]
    d = cms.tou_doc(code, *TOU_1630)
    big = {"peak": [(6, 8), (18, 22)], "flat": [(4, 6), (8, 11), (16, 18), (22, 24)], "valley": [(0, 4), (11, 16)]}
    small = {"peak": [(6, 8), (18, 22)], "flat": [(0, 6), (8, 11), (16, 18), (22, 24)], "valley": [(11, 16)],
             "sharp": [(19, 21)], "deep_valley": [(13, 15)]}
    rules = [([1, 2, 3, 4, 5, 9, 10, 11, 12], "normal", cms.segs_from_hours(big)),
             ([6, 7, 8], "normal", cms.segs_from_hours(small))]
    return d, cms.tou_rows(code, d, rules, TOU_ID_BASE)


TOU_NOTES = [
    "Source of record: 正镶白旗人民政府 (zxbq.gov.cn) repost of the monthly 内蒙古电力（集团）代理购电工商业用户电价表 images "
    "(impc.com.cn unreachable from the pipeline). Tables OCR'd with tesseract and cross-validated against the notice's "
    "formula row; repairs logged in data/raw/mengxi/announcements.json (ocr_corrections). 1.5倍 and 代理购电价格表 not loaded.",
    "TOU per 内发改价费字〔2023〕1630号 (2023-12-28, eff 2024-02-01; cited in note 2 of every 2025-11..2026-10 notice). "
    "大风季(1-5、9-12月): 峰 6-8、18-22, 平 4-6、8-11、16-18、22-24, 谷 0-4、11-16; 峰/谷 = 平 ± 68%/52% of 代理购电价格. "
    "小风季(6-8月): 峰 6-8、18-22, 平 0-6、8-11、16-18、22-24, 谷 11-16; ±54%/56%; 尖峰 19-21 (峰段价格 +20%), 深谷 13-15 "
    "(谷段价格 −20%). Float applies to 代理购电价格 only (输配、线损、系统运行费、基金不浮动).",
    "尖峰/深谷 are stored as printed in the 6-8月 tables (all rows incl. 不满1千伏); NULL in other months.",
]
