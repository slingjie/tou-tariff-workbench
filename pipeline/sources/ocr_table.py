"""OCR for tariff tables published as images (重庆 JPG galleries, 河北南网 image-only PDF posters, …).

Pipeline: image → binarise → detect ruled tables (horizontal lines grouped by a shared left border) →
pick the table whose title (OCR'd chi_sim, text just above the table) matches → detect column / row rules →
OCR every data cell with tesseract (digits whitelist, upscaled) and header / label cells with chi_sim →
map numeric columns by header text (FIELD_PATTERNS from sgcc_cms) with a per-region fallback order.

All results are cross-validated by the caller (component sum, province TOU float formula, voltage
monotonicity, TDP vs neighbouring months) – OCR output is never trusted on its own.
"""
import os, re
import numpy as np

from .. import parse_common as pc

os.environ.setdefault("OMP_THREAD_LIMIT", "1")   # tesseract threads thrash on small cells
TESSDATA = os.path.join(pc.ROOT, "pipeline", "tessdata")   # chi_sim + eng traineddata shipped with the repo
DIGITS = "-c tessedit_char_whitelist=0123456789.-"


def _tess(img, lang="eng", psm=7, extra=""):
    import pytesseract
    cfg = f"--psm {psm} {extra}"
    if os.path.exists(os.path.join(TESSDATA, f"{lang.split('+')[0]}.traineddata")):
        cfg = f'--tessdata-dir "{TESSDATA}" ' + cfg
    return pytesseract.image_to_string(img, lang=lang, config=cfg).strip()


def load_image(path):
    """Largest embedded bitmap of a PDF page (posters) or the image file itself; returns PIL 'L'."""
    from PIL import Image
    if path.lower().endswith(".pdf"):
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(path)
        best = None
        for page in doc:
            for o in page.get_objects():
                if o.type == 3:
                    b = o.get_bitmap(render=False).to_pil()
                    if best is None or b.width * b.height > best.width * best.height:
                        best = b
        if best is None:
            best = doc[0].render(scale=4).to_pil()
        return best.convert("L")
    return Image.open(path).convert("L")


def _groups(ix, gap=3):
    g = []
    for i in ix:
        if g and i - g[-1][-1] <= gap:
            g[-1].append(i)
        else:
            g.append([i])
    return g


def _longest_run(row):
    """(length, start, end) of the longest run of True in a 1-D bool array."""
    if not row.any():
        return 0, 0, 0
    d = np.diff(np.concatenate([[0], row.astype(np.int8), [0]]))
    starts, ends = np.where(d == 1)[0], np.where(d == -1)[0]
    k = int(np.argmax(ends - starts))
    return int(ends[k] - starts[k]), int(starts[k]), int(ends[k])


def find_tables(img, thr=150, min_frac=0.25):
    """Return (tables, binarised array). A table = dict(top, bottom, x0, x1, hlines=[y…]).
    Horizontal rules: pixel rows whose dark fraction > min_frac (rules are broken by merged cells, so total
    darkness is used, not run length). Two consecutive rules belong to the same table when >= 3 column
    rules cross the gap between them (titles / notes between tables have no vertical rules)."""
    a = np.array(img) < thr
    H, W = a.shape
    frac = a.mean(axis=1)
    ys = [int(np.mean(g)) for g in _groups(np.where(frac > min_frac)[0], gap=3)]
    tables = []
    for y in ys:
        if tables:
            t = tables[-1]
            yp = t["hlines"][-1]
            seg = a[yp + 3:y - 2, :]
            if seg.shape[0] > 0:
                full = np.where(seg.mean(axis=0) > 0.95)[0]
                if len(_groups(full, gap=2)) >= 3:
                    t["hlines"].append(y); t["bottom"] = y
                    continue
        tables.append({"top": y, "bottom": y, "hlines": [y]})
    out = []
    for t in tables:
        if len(t["hlines"]) < 4:
            continue
        seg = a[t["top"] + 2:t["bottom"] - 1, :]
        cols = np.where(seg.mean(axis=0) > 0.5)[0]
        if not len(cols):
            continue
        t["x0"], t["x1"] = int(cols.min()), int(cols.max())
        out.append(t)
    return out, a


def vlines(a, t, frac=0.6):
    """Column rules inside table t, measured over its lower half (header cells are merged)."""
    y0 = t["top"] + (t["bottom"] - t["top"]) // 2
    sub = a[y0:t["bottom"], t["x0"] - 3:t["x1"] + 4]
    cols = np.where(sub.mean(axis=0) > frac)[0]
    return [int(np.mean(g)) + t["x0"] - 3 for g in _groups(cols)]


def title_above(img, t, height=None):
    h = height or max(60, int((t["hlines"][1] - t["hlines"][0]) * 2.2))
    box = (t["x0"], max(0, t["top"] - h), t["x1"], t["top"] - 2)
    return re.sub(r"\s+", "", _tess(img.crop(box), "chi_sim", psm=6))


NUM_RE = re.compile(r"-?\d+(\.\d+)?")


def _prep(img, box, scale, pad, thr):
    from PIL import Image, ImageOps
    x0, y0, x1, y1 = box
    cell = img.crop((x0 + pad, y0 + pad, x1 - pad, y1 - pad))
    if cell.width < 5 or cell.height < 5:
        return None
    if scale != 1.0:
        cell = cell.resize((int(cell.width * scale), int(cell.height * scale)), Image.LANCZOS)
    if thr:
        cell = cell.point(lambda p: 0 if p < thr else 255)
    return ImageOps.expand(cell, border=15, fill=255)


def ocr_num(img, box, scale=1.0, pad=2):
    """Digits-only OCR of one cell. Tries grey (best for anti-aliased renders) then two thresholds
    (best against watermarks); returns the value most variants agree on."""
    probe = _prep(img, box, 1.0, pad, 140)
    if probe is None or np.array(probe).min() > 0:      # blank cell
        return None, ""
    votes = {}
    first = ""
    for thr in (None, 190, 160):
        c = _prep(img, box, scale, pad, thr)
        s = _tess(c, "eng", psm=7, extra=DIGITS).replace(" ", "")
        first = first or s
        if NUM_RE.fullmatch(s or ""):
            votes.setdefault(s, 0)
            votes[s] += 1 if thr else 1.5   # grey wins ties
    if not votes:
        return None, first
    s = max(votes, key=votes.get)
    return pc.num(s), s


def ocr_text(img, box, scale=1.0):
    from PIL import Image
    x0, y0, x1, y1 = box
    cell = img.crop((x0 + 3, y0 + 3, x1 - 3, y1 - 3))
    if cell.width < 5 or cell.height < 5:
        return ""
    if scale != 1.0:
        cell = cell.resize((int(cell.width * scale), int(cell.height * scale)), Image.LANCZOS)
    cell = cell.point(lambda p: 0 if p < 150 else 255)
    if np.array(cell).min() > 0:
        return ""
    return re.sub(r"\s+", "", _tess(cell, "chi_sim", psm=7))


def _has_rule(a, y, x0, x1, frac=0.6):
    band = a[max(0, y - 2):y + 3, x0 + 3:x1 - 2]
    return band.size and band.any(axis=0).mean() > frac


def read_table(img, t, a, scale=1.0, log=print):
    """OCR a ruled table.  Merged cells are handled per column: a row rule that is absent inside a column
    joins the cells above/below, the merged cell is OCR'd once and its value given to every row it spans.
    Returns dict(data=[(nums, raws)], cols, rows, voltage_digits=[…], n_label_cols)."""
    xs = vlines(a, t)
    cols = [(x0, x1) for x0, x1 in zip(xs, xs[1:]) if x1 - x0 > 8]
    ys = list(t["hlines"])
    # drop spurious rules (watermark strokes, underlines): a real row rule crosses most columns
    ys = [y for k, y in enumerate(ys) if k in (0, len(ys) - 1) or
          sum(1 for x0, x1 in cols if _has_rule(a, y, x0, x1)) >= 0.5 * len(cols)]
    rows = [(y0, y1) for y0, y1 in zip(ys, ys[1:]) if y1 - y0 > 8]
    nR, nC = len(rows), len(cols)
    # per-column segments
    seg_of = [[None] * nC for _ in range(nR)]
    segs = []
    for c, (x0, x1) in enumerate(cols):
        start = 0
        for r in range(1, nR + 1):
            if r == nR or _has_rule(a, rows[r][0], x0, x1):
                segs.append((c, start, r - 1))
                for rr in range(start, r):
                    seg_of[rr][c] = len(segs) - 1
                start = r
    seg_val = {}
    for k, (c, r0, r1) in enumerate(segs):
        box = (cols[c][0], rows[r0][0], cols[c][1], rows[r1][1])
        if r1 > r0:   # merged: crop to the ink band so tesseract sees one line
            sub = a[box[1] + 3:box[3] - 2, box[0] + 3:box[2] - 2]
            ink = np.where(sub.mean(axis=1) > 0.01)[0]
            if len(ink):
                y0 = box[1] + 3 + ink.min() - 4; y1 = box[1] + 3 + ink.max() + 5
                box = (box[0], max(box[1], y0), box[2], min(box[3], y1))
        seg_val[k] = ocr_num(img, box, scale)
    grid_num = [[seg_val[seg_of[r][c]][0] for c in range(nC)] for r in range(nR)]
    grid_raw = [[seg_val[seg_of[r][c]][1] for c in range(nC)] for r in range(nR)]
    merged = [[segs[seg_of[r][c]][1] != segs[seg_of[r][c]][2] for c in range(nC)] for r in range(nR)]
    # data rows: >= 1 decimal in un-merged cells (the 公式 row holds only integers) that look like prices
    is_data = [sum(1 for c in range(nC) if not merged[r][c] and grid_num[r][c] is not None
                   and "." in grid_raw[r][c] and 0 < abs(grid_num[r][c]) < 5) >= 1 for r in range(nR)]
    if True not in is_data:
        raise RuntimeError("OCR: no data rows")
    data_idx = [r for r in range(nR) if is_data[r]]
    dec_cols = [c for c in range(nC) if sum(1 for r in data_idx if grid_num[r][c] is not None and "." in grid_raw[r][c]) >= max(1, len(data_idx) // 3)]
    c_first = min(dec_cols)
    # drop empty trailing columns (image border / area right of a cropped table)
    c_end = nC
    while c_end > c_first + 1 and all(not grid_raw[r][c_end - 1] for r in data_idx) and \
            all(not grid_raw[r][c_end - 1] for r in range(nR)):
        c_end -= 1
    data = [(grid_num[r][c_first:c_end], grid_raw[r][c_first:c_end]) for r in data_idx]
    cols = cols[:c_end]
    vdig = [grid_raw[r][c_first - 1] if c_first >= 1 else "" for r in data_idx]
    return {"data": data, "cols": cols[c_first:], "rows": [rows[r] for r in data_idx], "voltage_digits": vdig,
            "all_raw": [grid_raw[r] for r in range(nR)], "all_num": [grid_num[r] for r in range(nR)],
            "n_label_cols": c_first, "merged_cols": [c - c_first for c in range(c_first, nC)
                                                    if all(merged[r][c] for r in data_idx)]}


def pick_table(img, tables, want, avoid=("1.5倍", "1.5", "转供电", "信息表")):
    """First table whose OCR'd title contains one of `want` and none of `avoid`."""
    for t in tables:
        title = title_above(img, t)
        t["title_ocr"] = title
        if any(w in title for w in want) and not any(x in title for x in avoid):
            return t
    return None


def edit_distance(a, b):
    a, b = a or "", b or ""
    d = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, d[0] = d[0], i
        for j, cb in enumerate(b, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (ca != cb))
    return d[-1]


# ======================================================================
# OCR → rows with formula cross-validation / single-digit repair
# ======================================================================
SHARED_F = ("purchase_price", "line_loss", "gov_fund", "system_op")


def _fmt_like(v, raw):
    nd = len(raw.split(".")[1]) if raw and "." in raw else 6
    return f"{v:.{nd}f}"


def _mode(vals):
    vals = [round(v, 6) for v in vals if v is not None]
    if not vals:
        return None
    best = max(set(vals), key=vals.count)
    return best if vals.count(best) >= 2 or len(vals) == 1 else None


def _cluster(cands, tol):
    """cands: [(value, source)] → (value, [sources]) of the largest cluster (ties → contains a direct read)."""
    best = None
    for v, _ in cands:
        members = [(w, src) for w, src in cands if abs(w - v) <= tol]
        key = (len({src for _, src in members}), any(src in ("energy_price", "flat") for _, src in members))
        if best is None or key > best[0]:
            best = (key, members)
    if not best:
        return None, []
    members = best[1]
    direct = [w for w, src in members if src in ("energy_price", "flat")]
    v = direct[0] if direct else sorted(w for w, _ in members)[len(members) // 2]
    return v, sorted({src for _, src in members})


def solve_rows(table, columns, layout, month, coef, base_kind="P+tdp", tol=2.5e-6, max_ed=2, log=print, info=None):
    """Turn read_table() output into tariff rows, cross-validating every OCR cell against the province's
    published relations:  energy = Σ(代理购电价格, 线损, 输配电价, 基金, 系统运行费) = 平段;  TOU = 平段 ± k·B
    with B = 代理购电价格 + 输配电价 (base_kind 'P+tdp', 重庆) or one monthly constant (base_kind 'month',
    河北南网 当月平均购电价格).  Each cell implies a value of the row's energy price; the largest agreeing cluster
    (≥ 2 independent cells) fixes it, then every cell is re-derived and a mismatching cell is repaired when
    ≥ 3 cells agree, or ≥ 2 agree and the repair differs from the OCR string by ≤ max_ed characters.
    coef: peak, valley (fractions of B) and optional sharp_on_peak / deep_on_valley. Repairs are returned."""
    if len(columns) != len(table["data"][0][0]):
        raise RuntimeError(f"OCR found {len(table['data'][0][0])} numeric columns, layout expects {len(columns)}")
    if len(layout) != len(table["data"]):
        raise RuntimeError(f"OCR found {len(table['data'])} data rows, layout expects {len(layout)}")
    rows, raws = [], []
    for (nums, rr), (tt, v) in zip(table["data"], layout):
        r = {f: n for f, n in zip(columns, nums)}
        raws.append({f: (s if re.search(r"\d", s or "") else "") for f, s in zip(columns, rr)})
        r.update(month=month, tariff_type=tt, voltage=v, voltage_rank=pc.voltage_rank(v))
        rows.append(r)
    fixes = []
    up, dn = coef["peak"], coef["valley"]
    K = {"energy_price": 0.0, "flat": 0.0, "peak": up, "valley": -dn}
    if "sharp_on_peak" in coef:
        K["sharp"] = up + coef["sharp_on_peak"] * (1 + up)
    if "deep_on_valley" in coef:
        K["deep_valley"] = -(dn + coef["deep_on_valley"] * (1 - dn))
    K = {f: k for f, k in K.items() if f in columns}

    # 1. shared (merged) components: S = Σ shared must equal energy − tdp (vote over rows)
    sh = {f: rows[0].get(f) for f in SHARED_F}
    for f, (v, raw) in (info or {}).items():
        if v is None:
            continue
        if sh.get(f) is None or (abs(sh[f] - v) > tol and edit_distance(raws[0].get(f, ""), raw) <= max_ed):
            # second, independent read of the same number in the 价格信息表
            pass
    alt = {f: v for f, (v, raw) in (info or {}).items() if v is not None}
    S_c = [(r[f] - r["tdp"], f"{i}{f}") for i, r in enumerate(rows) for f in ("energy_price", "flat")
           if r.get(f) is not None and r.get("tdp") is not None]
    S_obs, src = _cluster(S_c, tol)
    if S_obs is not None and len(src) >= 3 and None not in sh.values() and abs(sum(sh.values()) - S_obs) > tol:
        for f in SHARED_F:
            cand = S_obs - (sum(sh.values()) - sh[f])
            raw = raws[0].get(f, "")
            if edit_distance(raw, _fmt_like(cand, raw)) <= max_ed:
                fixes.append(f"shared {f}: OCR '{raw}' -> {_fmt_like(cand, raw)} (energy − tdp − other shared, {len(src)} cells agree)")
                for r in rows:
                    r[f] = round(cand, 8)
                sh[f] = cand
                break
    # reconcile with the 价格信息表 reads: try every combination of (main table, info table) per field and
    # keep the one whose sum matches S_obs; a still-missing single field is solved from S_obs
    if S_obs is not None and alt:
        import itertools
        opts = [[(sh.get(f), "main")] + ([(alt[f], "info")] if f in alt else []) for f in SHARED_F]
        best = None
        for combo in itertools.product(*opts):
            vals = [v for v, _ in combo]
            if vals.count(None) == 0 and abs(sum(vals) - S_obs) <= tol:
                best = combo; break
            if best is None and vals.count(None) <= 1:
                # one field unread or misread: solve it from S_obs if that is a small edit of what OCR saw
                for k in range(len(vals)):
                    if vals.count(None) == 1 and vals[k] is not None:
                        continue
                    cand = round(S_obs - sum(v for j, v in enumerate(vals) if j != k and v is not None), 6)
                    raw = raws[0].get(SHARED_F[k], "")
                    if -0.2 < cand < 0.2 and (vals[k] is None or edit_distance(raw, f"{cand:.6f}") <= max_ed):
                        best = tuple(list(combo[:k]) + [(cand, "derived")] + list(combo[k + 1:]))
                        break
        if best:
            for f, (v, src) in zip(SHARED_F, best):
                if sh.get(f) is None or abs(sh[f] - v) > tol:
                    fixes.append(f"shared {f}: OCR '{raws[0].get(f, '')}' -> {v} ({'价格信息表 read' if src == 'info' else 'energy − tdp − other shared'})")
                    for r in rows:
                        r[f] = v
                    sh[f] = v
    S = sum(sh.values()) if None not in sh.values() else S_obs
    P = sh.get("purchase_price")
    # 2. monthly base (河北南网): B from (peak−flat)/up etc. over all rows
    Bm = None
    if base_kind == "month":
        bc = []
        for i, r in enumerate(rows):
            for f, k in K.items():
                if k and r.get(f) is not None:
                    for g in ("energy_price", "flat"):
                        if r.get(g) is not None:
                            bc.append(((r[f] - r[g]) / k, f"{i}{f}{g}"))
        Bm, srcs = _cluster(bc, 1e-5)
        if Bm is None or len(srcs) < 3:
            raise RuntimeError("OCR: could not establish the monthly TOU base B")
    # 3. per row: vote on energy, then repair every cell
    for i, r in enumerate(rows):
        if base_kind == "P+tdp":
            if P is None or S is None:
                continue
            B0, c = P - S, 1.0          # B = P + tdp = P + e − S
        else:
            B0, c = Bm, 0.0
        cands = []
        for f, k in K.items():
            if r.get(f) is not None:
                cands.append(((r[f] - k * B0) / (1 + k * c), f))
        if r.get("tdp") is not None and S is not None:
            cands.append((r["tdp"] + S, "tdp"))
        e, agree = _cluster(cands, 4e-6)
        if e is None or len(agree) < 2:
            log(f"  OCR {month} {r['tariff_type']} {r['voltage']}: no 2 cells agree on the energy price {cands}")
            continue
        e = round(e, 8)
        B = B0 + c * e
        exp = {f: e + k * B for f, k in K.items()}
        if S is not None:
            exp["tdp"] = e - S
        for f, v in exp.items():
            if f not in columns:
                continue
            have, raw = r.get(f), raws[i].get(f, "")
            if have is None and not raw:
                continue           # blank cell (e.g. no 尖峰 this month / 不满1千伏 not time-of-use)
            if have is not None and abs(have - v) <= tol:
                continue
            nd = max(len(raw.split(".")[1]) if "." in raw else 0, 8 if base_kind == "month" else 6)
            new_s = f"{v:.{nd}f}"
            if len(agree) >= 3 or edit_distance(raw, new_s) <= max_ed:
                fixes.append(f"{r['tariff_type']} {r['voltage']} {f}: OCR '{raw}' -> {new_s} ({len(agree)} cells agree: {','.join(agree)})")
                r[f] = float(new_s)
            else:
                log(f"  OCR {month} {r['tariff_type']} {r['voltage']} {f}={have} vs derived {new_s} – left as read")
    # 4. regulated demand/capacity: a dropped decimal point (352 → 35.2)
    for i, r in enumerate(rows):
        for f in ("demand_price", "capacity_price"):
            v, raw = r.get(f), raws[i].get(f, "")
            if v is not None and v > 100 and "." not in raw:
                nv = v / 10
                fixes.append(f"{r['tariff_type']} {r['voltage']} {f}: OCR '{raw}' -> {nv} (decimal point dropped)")
                r[f] = nv
    return rows, fixes


def cached_parse(ann, region_code, fn, version=1):
    """OCR is slow (~25 s/table): cache parsed rows per month, keyed by the file's sha256 and a parser version,
    in data/raw/<region>/ocr_cache/<month>.json.  Re-runs reuse it; a new file or version re-OCRs."""
    import json
    d = os.path.join(pc.DATA, "raw", region_code, "ocr_cache")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{ann['month']}.json")
    key = {"sha256": ann.get("sha256"), "version": version}
    if os.path.exists(p):
        c = json.load(open(p, encoding="utf-8"))
        if c.get("key") == key and "rows" in c:
            for k, v in c.get("ann", {}).items():
                ann[k] = v
            return c["rows"]
    if os.path.exists(p):
        c = json.load(open(p, encoding="utf-8"))
        if c.get("key") == key and c.get("error"):
            ann["parse_method"] = None
            raise RuntimeError(f"(cached OCR failure) {c['error']}")
    try:
        rows = fn()
    except RuntimeError as e:
        json.dump({"key": key, "error": str(e)}, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        raise
    keep = {k: ann.get(k) for k in ("parse_method", "ocr_corrections", "ocr_voltage_digits", "effective_from", "effective_to")}
    json.dump({"key": key, "ann": keep, "rows": rows}, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return rows


def regulated_consensus(parsed, anns, fields=("demand_price", "capacity_price"), period_key=None):
    """需量/容量 prices are regulated and constant within a TDP period: replace a value that disagrees with
    the value read in the majority (>= 3) of the period's other months. Logged into ann['ocr_corrections']."""
    from collections import Counter
    period_key = period_key or (lambda m, rows: len(rows))
    groups = {}
    for m, rows in parsed.items():
        pk = period_key(m, rows)
        for r in rows:
            for f in fields:
                if r.get(f) is not None:
                    groups.setdefault((pk, r["category"], r["tariff_type"], r["voltage"], f), []).append((m, r))
    log = []
    for (pk, cat, tt, v, f), items in groups.items():
        cnt = Counter(r[f] for _, r in items)
        val, n = cnt.most_common(1)[0]
        if n < 2 or n <= len(items) / 2:
            continue
        for m, r in items:
            if r[f] != val:
                msg = f"{tt} {v} {f}: OCR {r[f]} -> {val} (regulated price, {n} of {len(items)} months of the period agree)"
                anns[m].setdefault("ocr_corrections", []).append(msg)
                log.append(f"{m} {msg}")
                r[f] = val
    return log


def read_info_table(img, tables, a, scale=3.0, want=("价格信息表", "信息表"), log=print):
    """代理购电价格信息表 → {序号: 数值}.  序号 = first integer column, 数值 = last column."""
    t = None
    for tb in tables:
        title = tb.get("title_ocr") or title_above(img, tb)
        tb["title_ocr"] = title
        if any(w in title for w in want) and "1.5" not in title:
            t = tb
            break
    if t is None:
        # title OCR failed: take the largest table above the tariff table
        cands = [tb for tb in tables if len(tb["hlines"]) >= 10 and "用户电价表" not in tb.get("title_ocr", "")]
        t = cands[0] if cands else None
    if t is None:
        return {}
    tab = read_table(img, t, a, scale=scale, log=log)
    out = {}
    for raws, nums in zip(tab["all_raw"], tab["all_num"]):
        seq = next((x for x in raws[:3] if x and re.fullmatch(r"\d{1,2}", x)), None)
        val = nums[-1] if nums else None
        if seq and val is not None and "." in (raws[-1] or "") and int(seq) not in out:
            out[int(seq)] = (val, raws[-1])
    return out
