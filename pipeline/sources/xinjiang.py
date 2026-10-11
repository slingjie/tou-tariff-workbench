"""国网新疆电力有限公司 代理购电工商业用户电价.

Quirks
* xj.sgcc.com.cn is unreachable; notices are the 95598.cn omg-static copies (网上国网 › 执行的电价和收费标准, region
  新疆), data/raw/xinjiang/seed.json (browser-collected 2026-10-11).  Each month is ONE poster image (JPG; 2025-12 PNG)
  holding 表1 代理购电工商业用户电价表 (loaded), 表2 1.5倍 (skipped), 表3 价格信息表.  Resolutions vary from 769 to 9450 px
  wide; every poster is resampled to 2270 px wide before OCR.
* Market-based TOU: 代理购电价格 is one merged multi-line cell "[均价] 其中: [尖峰 x] 高峰 x 平时段 x 低谷 x [深谷 x]";
  分时电度电价 X = 代理购电价格(X) + 线损 + 输配 + 基金 + 系统运行费 (formula row 7..11); 非分时 = 平时段 (1=9).
  Stored: energy_price = 非分时 (= 平时段), purchase_price = 平时段 代理购电价格 (= energy − 线损 − 输配 − 基金 − 系统运行费),
  period purchase prices in announcements.json (period_purchase_prices).  尖峰 printed in 1、7、11、12月, 深谷 in 5-8月
  (per 新发改规〔2023〕11号) — stored only when printed ('-' → NULL).
* OCR acceptance (every month): 非分时 == 平时段 on every row; 电度输配电价 == the period's regulated list row for row;
  each TOU column minus (线损+输配+基金+系统运行费) gives the same period purchase price on every row; and those
  derived period purchase prices (平/峰/谷[/尖/深]) must all appear in the separately OCR'd merged 代理购电价格 cell —
  this ties the merged 线损/基金/系统运行费 cells to an independent reading.  需量/容量 by majority within the period.
* Layout: 3rd TDP period (to 2026-07) 7 rows – 单一制 不满1千伏 / 1~10千伏 / 35千伏及以上, 两部制 1~10 / 35 / 110 /
  220千伏及以上; 4th period (2026-08 on, 发改价格〔2026〕1077号) 9 rows – 单一制 不满1 / 1~10 / 35 / 110 / 220千伏及以上.
"""
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from . import sgcc_cms as cms
from . import ocr_table as ot
from . import _northwest_util as nw
from .. import parse_common as pc

REGION = {"code": "xinjiang", "name": "新疆", "grid": "sgcc", "province": "新疆维吾尔自治区"}
TOU_ID_BASE = 75000
OCR_VERSION = 3   # cache key; v2 = ink-trimmed cell reader (+ solver fallback)
WIDTH = 2270

_months = cms.months_between

COLS = ["energy_price", "purchase_cell", "line_loss", "tdp", "gov_fund", "system_op",
        "sharp", "peak", "flat", "valley", "deep_valley", "demand_price", "capacity_price"]
LAYOUT = {7: [("单一制", "不满1千伏"), ("单一制", "1~10千伏"), ("单一制", "35千伏及以上"),
              ("两部制", "1~10千伏"), ("两部制", "35千伏"), ("两部制", "110千伏"), ("两部制", "220千伏及以上")],
          9: [("单一制", "不满1千伏"), ("单一制", "1~10千伏"), ("单一制", "35千伏"), ("单一制", "110千伏"),
              ("单一制", "220千伏及以上"),
              ("两部制", "1~10千伏"), ("两部制", "35千伏"), ("两部制", "110千伏"), ("两部制", "220千伏及以上")]}
TDP = {7: [0.1636, 0.1606, 0.1566, 0.1204, 0.1100, 0.0815, 0.0486],                        # 新发改能价〔2023〕243号
       9: [0.1636, 0.1606, 0.1566, 0.1221, 0.1059, 0.1202, 0.1098, 0.0808, 0.0485]}       # 发改价格〔2026〕1077号
REST = ("line_loss", "tdp", "gov_fund", "system_op")


def discover(start, end, log=print):
    return nw.seed_discover(REGION["code"], start, end, log)


def download(ann, log=print):
    return nw.download(REGION["code"], ann, log)


def policy_doc_row(ann):
    return nw.policy_doc_row(REGION["code"], ann)


def _image(path):
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    im = Image.open(path).convert("L")
    w, h = im.size
    return im.resize((WIDTH, int(round(h * WIDTH / w))), Image.LANCZOS)


def _orig(path):
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    return Image.open(path).convert("L")


def _orig_width(path):
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    with Image.open(path) as im:
        return im.size[0]


def _cell(img, a, box, scale=None, target_h=48):
    """Number in a cell, None for blank or a dash ('-').  The crop is inset past the cell rules, trimmed to the ink
    bounding box and rescaled to a fixed glyph height (resolutions differ ×12 between months); three binarisations
    vote (ocr_table.ocr_num's 2-px inset lets rule pixels eat leading digits of short values like '32')."""
    from PIL import Image, ImageOps
    x0, y0, x1, y1 = box
    ins = 5
    sub = a[y0 + ins:y1 - ins, x0 + ins:x1 - ins]
    if sub.size == 0:
        return None, ""
    rows = np.where(sub.mean(axis=1) > 0.01)[0]
    colsx = np.where(sub.mean(axis=0) > 0.01)[0]
    if len(rows) == 0:
        return None, ""
    if rows.max() - rows.min() + 1 <= 6:                 # a short horizontal bar
        return None, "-"
    bx0, bx1 = x0 + ins + colsx.min() - 4, x0 + ins + colsx.max() + 5
    by0, by1 = y0 + ins + rows.min() - 4, y0 + ins + rows.max() + 5
    cell = img.crop((bx0, by0, bx1, by1))
    f = target_h / max(1, cell.height)
    cell = cell.resize((max(1, int(cell.width * f)), target_h), Image.LANCZOS)
    votes, first = {}, ""
    for thr in (None, 170, 200):
        c2 = cell.point(lambda p, thr=thr: 0 if p < thr else 255) if thr else cell
        txt = ot._tess(ImageOps.expand(c2, border=20, fill=255), "eng", psm=7, extra=ot.DIGITS).replace(" ", "")
        first = first or txt
        if ot.NUM_RE.fullmatch(txt or ""):
            votes[txt] = votes.get(txt, 0) + (1.5 if thr is None else 1)
    if not votes:
        return None, first
    txt = max(votes, key=votes.get)
    return pc.num(txt), txt


def _read(img, scales=(1.5,)):
    tables, a = ot.find_tables(img, thr=170)
    cand = [t for t in tables if len(ot.vlines(a, t)) == 17]
    if not cand:
        tables, a = ot.find_tables(img, thr=190)
        cand = [t for t in tables if len(ot.vlines(a, t)) == 17]
    if not cand:
        raise RuntimeError("OCR: 表1 (17 column rules) not found")
    t = cand[0]
    xs = ot.vlines(a, t)
    cols = [(x0, x1) for x0, x1 in zip(xs, xs[1:]) if x1 - x0 > 8][3:]
    rx = cols[COLS.index("flat")]
    ys = [y for y in t["hlines"] if ot._has_rule(a, y, *rx)]
    bands = [b for b in list(zip(ys, ys[1:]))[3:] if b[1] - b[0] > 12]
    jobs = {}
    for c, (x0, x1) in enumerate(cols):
        if COLS[c] == "purchase_cell":
            continue
        if not any(ot._has_rule(a, b[0], x0, x1) for b in bands[1:]):
            y0, y1 = bands[0][0], bands[-1][1]
            sub = a[y0 + 3:y1 - 2, x0 + 3:x1 - 2]
            ink = np.where(sub.mean(axis=1) > 0.01)[0]
            if len(ink):
                jobs[("m", c)] = (x0, y0 + 3 + ink.min() - 4, x1, y0 + 3 + ink.max() + 5)
        else:
            for r, (y0, y1) in enumerate(bands):
                jobs[(r, c)] = (x0, y0, x1, y1)
    small = {COLS.index(k) for k in ("sharp", "deep_valley", "demand_price", "capacity_price")}

    def run(item):
        (k, c), box = item
        return (k, c), _cell(img, a, box)

    with ThreadPoolExecutor(6) as ex:
        res = dict(ex.map(run, jobs.items()))
    vals = [[None] * len(cols) for _ in bands]
    for (k, c), (v, s) in res.items():
        if k == "m":
            for r in range(len(bands)):
                vals[r][c] = v
        else:
            vals[k][c] = v
    # merged multi-line 代理购电价格 cell: all decimals it contains
    from PIL import Image
    x0, x1 = cols[1]
    y0, y1 = bands[0][0], bands[-1][1]
    nums = set()
    for sc in (2.0, 3.0):
        cell = img.crop((x0 + 3, y0 + 3, x1 - 3, y1 - 3))
        cell = cell.resize((int(cell.width * sc), int(cell.height * sc)), Image.LANCZOS)
        for thr in (None, 170):
            c2 = cell.point(lambda p, thr=thr: 0 if p < thr else 255) if thr else cell
            txt = ot._tess(c2, "eng", psm=6, extra=ot.DIGITS)
            nums |= {float(x) for x in re.findall(r"-?\d\.\d{4,6}", txt)}
    return vals, sorted(nums)


def _derive(vals):
    """Per-row period purchase prices from the TOU columns; returns (dict period->value, errors)."""
    errs, per = [], {}
    n = len(vals)
    if n not in LAYOUT:
        return {}, [f"{n} data rows, expected 7 or 9"]
    for i, (v, tdp) in enumerate(zip(vals, TDP[n])):
        rec = dict(zip(COLS, v))
        if rec["tdp"] is None or abs(rec["tdp"] - tdp) > 1e-9:
            errs.append(f"row {i + 1} tdp {rec['tdp']} != regulated {tdp}")
            continue
        if None in (rec["energy_price"], rec["flat"]) or abs(rec["energy_price"] - rec["flat"]) > 1e-9:
            errs.append(f"row {i + 1} 非分时 {rec['energy_price']} != 平时段 {rec['flat']}")
        if any(rec[k] is None for k in REST):
            errs.append(f"row {i + 1} merged component missing")
            continue
        rest = sum(rec[k] for k in REST)
        for k in ("sharp", "peak", "flat", "valley", "deep_valley"):
            if rec[k] is not None:
                per.setdefault(k, []).append(round(rec[k] - rest, 6))
            elif k in ("peak", "flat", "valley"):
                errs.append(f"row {i + 1} {k} blank")
    out = {}
    for k, lst in per.items():
        c = Counter(lst)
        if len(c) > 1:
            errs.append(f"{k}: derived purchase differs across rows {dict(c)}")
        out[k] = c.most_common(1)[0][0]
    for k in ("sharp", "deep_valley"):
        if k in per and len(per[k]) != n:
            errs.append(f"{k} printed on {len(per[k])} of {n} rows")
    return out, errs


def parse(ann, template=None, log=print):
    def run():
        img = _image(ann["file_path"])
        if _orig_width(ann["file_path"]) < 1200:     # low-res poster: direct OCR never passes, go to the solver
            vals, nums, errs = None, [], ["low-resolution poster"]
        else:
            vals, nums = _read(img)
            per, errs = _derive(vals)
        if not errs:
            missing = [k for k, v in per.items() if not any(abs(v - x) < 5e-7 for x in nums)]
            if missing:
                errs.append(f"derived purchase {[(k, per[k]) for k in missing]} not in 代理购电价格 cell OCR {nums}")
        method = "OCR (tesseract) of poster image 表1, resampled to 2270 px"
        if errs:
            log(f"  direct OCR failed ({errs[:2]}); candidate solver")
            vals, nums, _ = _solve(img, ann, log, orig=_orig(ann["file_path"]))
            per, errs = _derive(vals)
            if not errs:
                missing = [k for k, v in per.items() if not any(abs(v - x) < 5e-7 for x in nums)]
                if missing:
                    errs.append(f"derived purchase {missing} not in 代理购电价格 cell OCR")
            method = ("OCR (tesseract, low-resolution poster) – multi-variant candidates solved against the table "
                      "arithmetic (1=9, X = 代理购电价格(X) + 3+4+5+6) and the merged 代理购电价格 cell")
        if errs:
            raise RuntimeError(f"OCR checks failed: {errs[:4]}")
        out = []
        for v, (tt, volt) in zip(vals, LAYOUT[len(vals)]):
            rec = dict(zip(COLS, v))
            out.append({"month": ann["month"], "category": "工商业用电", "tariff_type": tt, "voltage": volt,
                        "voltage_rank": pc.voltage_rank(volt), "energy_price": rec["energy_price"],
                        "purchase_price": per["flat"], "line_loss": rec["line_loss"], "tdp": rec["tdp"],
                        "system_op": rec["system_op"], "gov_fund": rec["gov_fund"], "sharp": rec["sharp"],
                        "peak": rec["peak"], "flat": rec["flat"], "valley": rec["valley"],
                        "deep_valley": rec["deep_valley"],
                        "demand_price": rec["demand_price"] if tt == "两部制" else None,
                        "capacity_price": rec["capacity_price"] if tt == "两部制" else None,
                        "_period_purchase": per})
        ann["parse_method"] = method
        return out

    rows = ot.cached_parse(ann, REGION["code"], run, version=OCR_VERSION)
    ann["period_purchase_prices"] = rows[0].get("_period_purchase")
    ann["effective_from"], ann["effective_to"] = cms.effective_dates("", ann["month"])
    return rows


# ---------------------------------------------------------------- low-resolution fallback (2025-11 967 px, 2025-12 769 px)
def _variants(txt):
    out = set()
    t = (txt or "").replace(" ", "")
    if not t:
        return out
    if ot.NUM_RE.fullmatch(t):
        out.add(float(t))
    d = t.replace(".", "").replace("-", "")
    if d.isdigit():
        if len(d) in (5, 6, 7) and d[0] in "09":
            out.add(float("0." + d[1:]))          # '0.464501' read as '0464501' / '9464501'
        if len(d) == 6:
            out.add(float("0." + d))              # leading '0.' dropped
    if re.fullmatch(r"9\.\d+", t):
        out.add(float("0" + t[1:]))               # '0.' read as '9.'
    return {v for v in out if 0 < v < 100}


def _cands(img, a, box, orig=None, f=1.0):
    """Candidate readings of one cell.  Geometry (ink box) from the 2270-px image ``img``/``a``; pixels read from the
    ORIGINAL poster ``orig`` (box scaled by f) — upscaled-then-resampled low-res glyphs confuse 1/2, 7/8.  12 OCR
    variants vote; readings with < 30 % of the top vote are dropped."""
    from PIL import Image, ImageOps
    x0, y0, x1, y1 = box
    ins = 5
    sub = a[y0 + ins:y1 - ins, x0 + ins:x1 - ins]
    rows = np.where(sub.mean(axis=1) > 0.01)[0] if sub.size else []
    if len(rows) == 0:
        return "blank", {}
    if rows.max() - rows.min() + 1 <= 6:
        return "dash", {}
    colsx = np.where(sub.mean(axis=0) > 0.01)[0]
    bb = (x0 + ins + colsx.min() - 4, y0 + ins + rows.min() - 4, x0 + ins + colsx.max() + 5, y0 + ins + rows.max() + 5)
    src = orig if orig is not None else img
    base = src.crop(tuple(int(round(v * f)) for v in bb))
    votes = Counter()
    for h in (48, 72):
        for rs in (Image.LANCZOS, Image.BICUBIC):
            c = base.resize((max(1, int(base.width * h / max(1, base.height))), h), rs)
            for thr in (None, 150, 210):
                c2 = c.point(lambda p, thr=thr: 0 if p < thr else 255) if thr else c
                for v in _variants(ot._tess(ImageOps.expand(c2, border=20, fill=255), "eng", psm=7, extra=ot.DIGITS)):
                    votes[v] += 1
    if not votes:
        return "num", {}
    top = max(votes.values())
    return "num", {v: k / top for v, k in votes.items() if k >= 0.3 * top}

GOV_FUND_NOTE = 0.0041


def _merged_box(a, x0, x1, y0, y1):
    """Box of the printed value inside a tall merged cell: the ink row-group (watermark fragments excluded) with the
    most ink, preferring the one nearest the vertical centre."""
    sub = a[y0 + 3:y1 - 2, x0 + 5:x1 - 5]
    prof = sub.mean(axis=1)
    idx = np.where(prof > 0.02)[0]
    groups = []
    for i in idx:
        if groups and i - groups[-1][-1] <= 3:
            groups[-1].append(i)
        else:
            groups.append([i])
    if not groups:
        return (x0, y0, x1, y1)
    mid = (y1 - y0) / 2
    g = max(groups, key=lambda g: prof[g].sum() - 0.002 * abs((g[0] + g[-1]) / 2 - mid))
    return (x0, y0 + 3 + g[0] - 6, x1, y0 + 3 + g[-1] + 7)


def _solve(img, ann, log=print, orig=None):
    from itertools import product
    from PIL import Image
    f = orig.size[0] / img.size[0] if orig is not None else 1.0
    orig = orig if orig is not None else img
    tables, a = ot.find_tables(img, thr=170)
    cand = [t for t in tables if len(ot.vlines(a, t)) == 17]
    if not cand:
        raise RuntimeError("OCR: 表1 not found")
    t = cand[0]
    xs = ot.vlines(a, t)
    cols = [(x0, x1) for x0, x1 in zip(xs, xs[1:]) if x1 - x0 > 8][3:]
    rx = cols[COLS.index("flat")]
    ys = [y for y in t["hlines"] if ot._has_rule(a, y, *rx)]
    bands = [b for b in list(zip(ys, ys[1:]))[3:] if b[1] - b[0] > 12]
    n = len(bands)
    if n not in LAYOUT:
        raise RuntimeError(f"solver: {n} rows")
    ci = {k: COLS.index(k) for k in COLS}
    jobs = {}
    for k in ("line_loss", "gov_fund", "system_op"):
        x0, x1 = cols[ci[k]]
        y0, y1 = bands[0][0], bands[-1][1]
        jobs[("m", k)] = _merged_box(a, x0, x1, y0, y1)
    for r, (y0, y1) in enumerate(bands):
        for k in ("energy_price", "tdp", "sharp", "peak", "flat", "valley", "deep_valley", "demand_price",
                  "capacity_price"):
            jobs[(r, k)] = (cols[ci[k]][0], y0, cols[ci[k]][1], y1)
    with ThreadPoolExecutor(6) as ex:
        res = dict(zip(jobs, ex.map(lambda b: _cands(img, a, b, orig, f), jobs.values())))
    # merged 代理购电价格 cell
    x0, x1 = cols[ci["purchase_cell"]]
    y0, y1 = bands[0][0], bands[-1][1]
    nums = set()
    cell = orig.crop(tuple(int(round(v * f)) for v in (x0 + 3, y0 + 3, x1 - 3, y1 - 3)))
    for sc in (2.0, 3.0, 4.0):
        c = cell.resize((int(cell.width * sc), int(cell.height * sc)), Image.LANCZOS)
        for thr in (None, 170, 200):
            c2 = c.point(lambda p, thr=thr: 0 if p < thr else 255) if thr else c
            for tok in re.findall(r"[\d.]{5,9}", ot._tess(c2, "eng", psm=6, extra=ot.DIGITS)):
                nums |= {v for v in _variants(tok) if 0 < v < 2}
    tdp = TDP[n]
    fixes = []
    for r in range(n):
        if tdp[r] not in res[(r, "tdp")][1]:
            fixes.append(f"row {r + 1} tdp OCR {sorted(res[(r, 'tdp')][1])} -> regulated {tdp[r]}")
    C = {k: sorted(v for v in res[("m", k)][1] if v < 0.5) for k in ("line_loss", "gov_fund", "system_op")}
    if not C["gov_fund"]:
        # note 1 of every notice: 政府性基金及附加 = 大中型水库移民后期扶持基金 0.0021 + 可再生能源电价附加 0.002 元/kWh
        C["gov_fund"] = [GOV_FUND_NOTE]
        fixes.append(f"merged gov_fund unreadable -> {GOV_FUND_NOTE} as stated in note 1 (0.0021 + 0.002)")
    if not all(C.values()):
        raise RuntimeError(f"solver: merged cell unread {C}")
    near = lambda v, S: any(abs(v - x) < 5e-7 for x in S)
    wt = lambda v, S: max([S[x] for x in S if abs(v - x) < 5e-7] or [0])    # vote share of the matching reading
    best = None
    for ll, gf, so in product(C["line_loss"], C["gov_fund"], C["system_op"]):
        rest = [ll + gf + so + tdp[r] for r in range(n)]
        for p in nums:
            sup = [wt(round(p + rest[r], 6), res[(r, "energy_price")][1]) +
                   wt(round(p + rest[r], 6), res[(r, "flat")][1]) for r in range(n)]
            score = (sum(x > 0 for x in sup), sum(sup))
            if best is None or score > best[0]:
                best = (score, (ll, gf, so, p), sup)
    (nrows, total), (ll, gf, so, pflat), sup = best
    if nrows < n - 1 or total < 1.2 * n:
        raise RuntimeError(f"solver: no consistent 平段 solution (rows {nrows}/{n}, support {total:.2f}/{2 * n})")
    rest = [ll + gf + so + tdp[r] for r in range(n)]
    for r in range(n):
        if sup[r] == 0:
            fixes.append(f"row {r + 1} 非分时/平时段 unreadable -> {round(pflat + rest[r], 6)} "
                         f"(purchase(平) {pflat} + components)")
    per = {"flat": pflat}
    out = {r: {"energy_price": round(pflat + rest[r], 6), "flat": round(pflat + rest[r], 6)} for r in range(n)}
    for k in ("sharp", "peak", "valley", "deep_valley"):
        kinds = [res[(r, k)][0] for r in range(n)]
        if all(x in ("dash", "blank") for x in kinds):
            for r in range(n):
                out[r][k] = None
            continue
        # candidate period purchase prices: the merged cell's numbers, plus cell value − components per row
        pool = set(nums) | {round(v - rest[r], 6) for r in range(n) for v in res[(r, k)][1] if v < 2}
        scored = []
        for p in pool:
            ws = [wt(round(p + rest[r], 6), res[(r, k)][1]) for r in range(n)]
            sup = sum(w > 0 for w in ws)
            ic = near(p, nums)
            scored.append((ic and sup >= (n + 1) // 2, sup, sum(ws), ic, p))
        _, sup, _, in_cell, p = max(scored) if scored else (False, 0, 0, False, None)
        # accept: read in the merged cell and on ≥ half the rows, or (cell unreadable) on all rows but one
        if not ((in_cell and sup >= (n + 1) // 2) or sup >= n - 1):
            raise RuntimeError(f"solver: {k} unresolved (best support {sup}/{n}, in cell {in_cell})")
        if not in_cell:
            nums = sorted(set(nums) | {p})
            fixes.append(f"purchase({k}) {p} not read in the merged cell; agreed by {sup}/{n} rows")
        per[k] = p
        for r in range(n):
            v = round(p + rest[r], 6)
            if not near(v, res[(r, k)][1]):
                fixes.append(f"row {r + 1} {k} OCR {sorted(res[(r, k)][1])} -> {v} (purchase({k}) {p} + components)")
            out[r][k] = v
    for r in range(n):
        for k in ("demand_price", "capacity_price"):
            vals = sorted(v for v in res[(r, k)][1] if 5 <= v <= 60)
            out[r][k] = vals[0] if len(vals) == 1 else (Counter(vals).most_common(1)[0][0] if vals else None)
    vals = []
    for r in range(n):
        o = out[r]
        vals.append([o["energy_price"], None, ll, tdp[r], gf, so, o["sharp"], o["peak"], o["flat"], o["valley"],
                     o["deep_valley"], o["demand_price"], o["capacity_price"]])
    ann["ocr_corrections"] = fixes
    log(f"  solver: {len(fixes)} cell repairs")
    return vals, sorted(nums), per


def postprocess(parsed, anns):
    return ot.regulated_consensus(parsed, anns, period_key=lambda m, rows: len(rows))


def extra_validate(rows):
    fails = []
    for r in rows:
        per = r.get("_period_purchase") or {}
        rest = sum(r[k] for k in REST)
        for k, v in per.items():
            if r.get(k) is not None and abs(r[k] - rest - v) > 5e-7:
                fails.append({"month": r["month"], "category": r["category"], "tariff_type": r["tariff_type"],
                              "voltage": r["voltage"], "check": "period_formula",
                              "detail": f"{k}={r[k]} != purchase({k}) {v} + components"})
        if r["tariff_type"] == "两部制" and (r["demand_price"] is None or r["capacity_price"] is None):
            fails.append({"month": r["month"], "category": r["category"], "tariff_type": r["tariff_type"],
                          "voltage": r["voltage"], "check": "demand_capacity_missing", "detail": "OCR blank"})
    return fails


# ---------------------------------------------------------------- TOU: 新发改规〔2023〕11号 (eff 2023-10-01)
TOU_11 = ("https://xjdrc.xinjiang.gov.cn/xjfgw/c108361/202312/e5c0f50c7028486ebe36ea253829b340.shtml",
          "自治区发展改革委关于进一步完善分时电价有关事宜的通知", "新发改规〔2023〕11号", "2023-08-30", "2023-10-01")


def tou_rules():
    code = REGION["code"]
    doc = nw.tou_doc(code, *TOU_11)
    base = {"peak": [(8, 11), (19, 24)], "flat": [(11, 13), (17, 19), (0, 4)], "valley": [(4, 8), (13, 17)]}

    def spec(sharp=None, deep=False):
        s = {k: list(v) for k, v in base.items()}
        if sharp:
            s["sharp"] = [sharp]
        if deep:
            s["deep_valley"] = [(14, 16)]
        return s
    rules = [([1, 11, 12], "normal", cms.segs_from_hours(spec(sharp=(19, 21)))),
             ([7], "normal", cms.segs_from_hours(spec(sharp=(21, 23), deep=True))),
             ([5, 6, 8], "normal", cms.segs_from_hours(spec(deep=True))),
             ([2, 3, 4, 9, 10], "normal", cms.segs_from_hours(spec()))]
    return doc, cms.tou_rows(code, doc, rules, TOU_ID_BASE)


TOU_NOTES = [
    "TOU periods per 新发改规〔2023〕11号 (自治区发展改革委关于进一步完善分时电价有关事宜的通知, 2023-08-30, eff 2023-10-01), restated in "
    "note 2 of every monthly notice 2025-11..2026-10: 高峰 8:00-11:00、19:00-24:00; 平 11:00-13:00、17:00-19:00、0:00-4:00; 低谷 4:00-8:00、"
    "13:00-17:00; 尖峰 = 7月 21:00-23:00 and 1、11、12月 19:00-21:00 (taken from 高峰); 深谷 = 5-8月 14:00-16:00 (taken from 低谷).",
    "Float: 代理购电价格 floats (高峰/低谷 ±75%, 尖峰 +100%, 深谷 −90% on 平 per the policy); the table prints the resulting period "
    "代理购电价格 and 分时电度电价 = period 代理购电价格 + 线损 + 输配 + 基金 + 系统运行费. energy_price = 非分时 = 平时段; "
    "purchase_price = 平时段 代理购电价格; period purchase prices in announcements.json.",
    "尖峰/深谷 stored only where printed in 表1 ('-' → NULL).  1.5倍 table (表2) not loaded.",
    "TDP: 3rd period 新发改能价〔2023〕243号 (to 2026-07), 4th period 发改价格〔2026〕1077号 (from 2026-08; single-part gains 110 / 220千伏 rows).",
    "Source: 95598.cn omg-static poster images (OCR, tesseract); acceptance checks described in pipeline/sources/xinjiang.py.",
]
