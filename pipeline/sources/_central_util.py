"""Shared helpers for the 'central' group (山东 shandong, 河南 henan, 湖北 hubei, 湖南 hunan).

All four provincial sgcc hosts are unreachable from the pipeline network, so the monthly notices come from the
State Grid national portal 95598.cn (网上国网 › 信息公开 › 执行的电价和收费标准, region selector); the omg-static
file URLs were collected with the Hark browser into data/raw/<code>/seed.json (static files, curl-downloadable,
no extension in the URL).  Every notice in this group is an image (JPG / PNG poster, image-only PDF, or a
vector PDF whose table numbers are outlined glyphs with no text layer), so tables are OCR'd.

Table detection here is stricter than ocr_table.find_tables: horizontal / vertical rules are found as long dark
runs (run length >= L px), which is robust against coloured header fills and watermarks; the resulting table
geometry is then handed to ocr_table.read_table (cell-by-cell digit OCR, merged-cell handling).
"""
import json
import os
import re

import numpy as np

from . import ocr_table as ot
from .. import parse_common as pc

MAGIC = {b"%PDF": "pdf", b"\xff\xd8": "jpg", b"\x89PNG": "png"}


# ------------------------------------------------------------------ seed / download
def seed_discover(code, start, end, log=print):
    """Announcements from data/raw/<code>/seed.json (collected from 95598.cn with the browser)."""
    path = os.path.join(pc.DATA, "raw", code, "seed.json")
    seed = json.load(open(path, encoding="utf-8"))
    wanted = set(pc_months(start, end))
    out = []
    for a in seed["articles"]:
        if a["month"] in wanted:
            ann = dict(a)
            ann["discovered_via"] = "seed (95598.cn listing via Hark browser)"
            out.append(ann)
            log(f"  {a['month']}: {a['file_url']}")
    return sorted(out, key=lambda x: x["month"])


def pc_months(start, end):
    y, m = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def download(code, ann, log=print):
    """Save to data/raw/<code>/<key>.<ext> (ext sniffed from magic bytes); combined multi-month files are
    shared via ann['file_key']."""
    raw = os.path.join(pc.DATA, "raw", code)
    os.makedirs(raw, exist_ok=True)
    key = ann.get("file_key") or ann["month"]
    for ext in ("pdf", "jpg", "png"):
        p = os.path.join(raw, f"{key}.{ext}")
        if os.path.exists(p) and os.path.getsize(p) > 1000:
            log(f"  skip (exists) {os.path.relpath(p, pc.ROOT)}")
            break
    else:
        r = pc.http_get(ann["file_url"], timeout=90)
        ext = next((e for mg, e in MAGIC.items() if r.content.startswith(mg)), None)
        if not ext:
            raise RuntimeError(f"unexpected file signature {r.content[:8]!r} for {ann['file_url']}")
        p = os.path.join(raw, f"{key}.{ext}")
        open(p, "wb").write(r.content)
        log(f"  downloaded {os.path.relpath(p, pc.ROOT)} ({len(r.content)} bytes)")
    ann["file_path"] = p
    ann["file_ext"] = p.rsplit(".", 1)[-1]
    ann["sha256"] = pc.sha256_file(p)
    return p


def policy_doc_row(code, ann):
    return {"id": pc.doc_id(ann["file_url"] + (f"#page={ann['page'] + 1}" if "page" in ann else "")),
            "region_code": code, "kind": "agency_price", "title": ann["title"], "doc_no": None,
            "publish_date": ann.get("publish_date"),
            "effective_from": ann.get("effective_from") or f"{ann['month']}-01",
            "effective_to": ann.get("effective_to"),
            "source_url": ann["file_url"] + (f"#page={ann['page'] + 1}" if "page" in ann else ""),
            "file_path": os.path.relpath(ann["file_path"], pc.ROOT), "sha256": ann["sha256"],
            "fetched_at": pc.now_iso()}


# ------------------------------------------------------------------ images
def page_image(path, page=0, scale=None, target_w=None):
    """PIL 'L' image of a poster: image file as is; PDF page rendered (scale or target width)."""
    from PIL import Image
    if open(path, "rb").read(4) != b"%PDF":
        return Image.open(path).convert("L")
    import pypdfium2 as pdfium
    pg = pdfium.PdfDocument(path)[page]
    if scale is None:
        scale = (target_w or 3000) / pg.get_size()[0]
    return pg.render(scale=scale).to_pil().convert("L")


def _runs(mask, L, axis):
    """Boolean mask of pixels that belong to a dark run of length >= L along axis."""
    m = mask if axis == 1 else mask.T
    H, W = m.shape
    if W <= L:
        return np.zeros_like(mask)
    cs = np.concatenate([np.zeros((H, 1), np.int32), np.cumsum(m, axis=1, dtype=np.int32)], axis=1)
    full = (cs[:, L:] - cs[:, :-L]) == L            # window [j, j+L) all dark
    out = np.zeros_like(m)
    # dilate the window starts back over their L pixels (prefix-sum of starts minus shifted)
    st = np.concatenate([np.zeros((H, 1), np.int32), np.cumsum(full, axis=1, dtype=np.int32)], axis=1)
    idx = np.arange(W)
    lo = np.clip(idx - L + 1, 0, full.shape[1])
    hi = np.clip(idx + 1, 0, full.shape[1])
    out = (st[:, hi] - st[:, lo]) > 0
    return out if axis == 1 else out.T


def find_tables(img, thr=170, L=None, min_frac=0.12, min_rows=4):
    """Ruled tables via long-run rule detection. Returns (tables, masks) where masks = dict(a=binarised,
    h=horizontal-rule pixels, v=vertical-rule pixels)."""
    a = np.array(img) < thr
    H, W = a.shape
    L = L or max(25, W // 60)
    hm = _runs(a, L, 1)
    vm = _runs(a, max(15, L // 2), 0)
    frac = hm.mean(axis=1)
    ys = [int(np.mean(g)) for g in ot._groups(np.where(frac > min_frac)[0], gap=3)]
    tables = []
    for y in ys:
        if tables:
            t = tables[-1]
            yp = t["hlines"][-1]
            seg = vm[yp + 3:y - 2, :]
            if seg.shape[0] > 0 and len(ot._groups(np.where(seg.mean(axis=0) > 0.9)[0], gap=2)) >= 3:
                t["hlines"].append(y); t["bottom"] = y
                continue
        tables.append({"top": y, "bottom": y, "hlines": [y]})
    out = []
    for t in tables:
        if len(t["hlines"]) < min_rows:
            continue
        seg = vm[t["top"] + 2:t["bottom"] - 1, :]
        cols = np.where(seg.mean(axis=0) > 0.5)[0]
        if not len(cols):
            continue
        t["x0"], t["x1"] = int(cols.min()), int(cols.max())
        out.append(t)
    return out, {"a": a, "h": hm, "v": vm}


def read(img, t, masks, scale=1.5, vcov=0.3):
    """ocr_table.read_table with rules taken from the long-run masks (columns: vertical rule covering
    >= vcov of the table height; a row rule exists inside a column when it spans >= 60% of the column)."""
    a, hm, vm = masks["a"], masks["h"], masks["v"]
    sub = vm[t["top"]:t["bottom"] + 1, :]
    xs = [int(np.mean(g)) for g in ot._groups(np.where(sub.mean(axis=0) > vcov)[0], gap=3)]
    cols = [(x0, x1) for x0, x1 in zip(xs, xs[1:]) if x1 - x0 > 8]
    ys = list(t["hlines"])

    def has_rule(y, x0, x1):
        band = hm[max(0, y - 3):y + 4, x0 + 3:x1 - 2]
        return band.size and band.any(axis=0).mean() > 0.6
    rows = [(y0, y1) for y0, y1 in zip(ys, ys[1:]) if y1 - y0 > 8]
    nR, nC = len(rows), len(cols)
    seg_of = [[None] * nC for _ in range(nR)]
    segs = []
    for c, (x0, x1) in enumerate(cols):
        start = 0
        for r in range(1, nR + 1):
            if r == nR or has_rule(rows[r][0], x0, x1):
                segs.append((c, start, r - 1))
                for rr in range(start, r):
                    seg_of[rr][c] = len(segs) - 1
                start = r
    seg_val = {}
    for k, (c, r0, r1) in enumerate(segs):
        box = (cols[c][0], rows[r0][0], cols[c][1], rows[r1][1])
        sub = a[box[1] + 4:box[3] - 3, box[0] + 4:box[2] - 3] & ~hm[box[1] + 4:box[3] - 3, box[0] + 4:box[2] - 3]
        ink = np.where(sub.mean(axis=1) > 0.01)[0] if sub.size else []
        if len(ink):   # crop to the ink band so tesseract sees one line (merged cells, tall rows)
            y0 = box[1] + 4 + ink.min() - 5; y1 = box[1] + 4 + ink.max() + 6
            box = (box[0], max(box[1], y0), box[2], min(box[3], y1))
        # inset more than ocr_table's default (2 px) so thin cell borders are not read as a leading '1'
        seg_val[k] = ot.ocr_num(img, box, scale, pad=max(2, round(img.width / 800)))
    grid_num = [[seg_val[seg_of[r][c]][0] for c in range(nC)] for r in range(nR)]
    grid_raw = [[seg_val[seg_of[r][c]][1] for c in range(nC)] for r in range(nR)]
    merged = [[segs[seg_of[r][c]][1] != segs[seg_of[r][c]][2] for c in range(nC)] for r in range(nR)]
    is_data = [sum(1 for c in range(nC) if not merged[r][c] and grid_num[r][c] is not None
                   and "." in grid_raw[r][c] and 0 < abs(grid_num[r][c]) < 5) >= 1 for r in range(nR)]
    if True not in is_data:
        raise RuntimeError(f"OCR: no data rows ({nR} rows x {nC} cols)")
    data_idx = [r for r in range(nR) if is_data[r]]
    dec_cols = [c for c in range(nC) if sum(1 for r in data_idx if grid_num[r][c] is not None
                                             and "." in grid_raw[r][c]) >= max(1, len(data_idx) // 3)]
    c_first = min(dec_cols)
    return {"data": [(grid_num[r][c_first:], grid_raw[r][c_first:]) for r in data_idx],
            "cols": cols[c_first:], "rows": [rows[r] for r in data_idx],
            "labels": [grid_raw[r][:c_first] for r in data_idx], "ncols_all": nC, "c_first": c_first}


def head_text(img, t, frac=0.06):
    """chi_sim OCR of the band above a table (title + 执行时间)."""
    h = int(img.height * frac)
    return re.sub(r"\s+", "", ot._tess(img.crop((t["x0"], max(0, t["top"] - h), t["x1"], t["top"] - 2)),
                                       "chi_sim", psm=6))


# ------------------------------------------------------------------ cross-validation
def cluster(vals, tol):
    """Largest group of (value, src) within tol; returns (median value, [src])."""
    best = (None, [])
    for v, _ in vals:
        mem = [(w, s) for w, s in vals if abs(w - v) <= tol]
        if len(mem) > len(best[1]):
            ws = sorted(w for w, _ in mem)
            best = (ws[len(ws) // 2], [s for _, s in mem])
    return best


def fix_shared(rows, raws, fields, tol, fixes):
    """Merged-cell components (same for every row) – a field read differently across rows takes the majority."""
    for f in fields:
        vals = [(r.get(f), i) for i, r in enumerate(rows) if r.get(f) is not None]
        if not vals:
            continue
        v, src = cluster(vals, tol)
        for r in rows:
            if r.get(f) is None or abs(r[f] - v) > tol:
                fixes.append(f"{r['tariff_type']} {r['voltage']} {f}: {r.get(f)} -> {v} (shared cell, majority)")
                r[f] = v


def solve_tou(rows, raws, comp, base_fn, coef, nd, tol, fixes, log=print, flat_is_energy=True):
    """Per row: energy = Σ comp = flat; TOU field = flat + k·B(row) with k from coef (published float).
    Each available cell implies an energy value; the largest agreeing cluster (>= 2 independent cells) fixes
    energy; then a disagreeing cell is repaired (logged).  Returns number of rows that could not be verified."""
    bad = 0
    for i, r in enumerate(rows):
        B = base_fn(r)
        cands = []
        if r.get("energy_price") is not None:
            cands.append((r["energy_price"], "energy_price"))
        if flat_is_energy and r.get("flat") is not None:
            cands.append((r["flat"], "flat"))
        if None not in [r.get(c) for c in comp]:
            cands.append((round(sum(r[c] for c in comp), 10), "Σcomponents"))
        if B is not None:
            for f, k in coef.items():
                if r.get(f) is not None:
                    cands.append((r[f] - k * B, f))
        e, agree = cluster(cands, tol)
        if e is None or len(agree) < 2:
            log(f"  !! {r['month']} {r['tariff_type']} {r['voltage']}: no 2 cells agree {cands}")
            bad += 1
            continue
        exp = {"energy_price": e}
        if flat_is_energy:
            exp["flat"] = e
        if B is not None:
            exp.update({f: e + k * B for f, k in coef.items()})
        for f, v in exp.items():
            raw = raws[i].get(f, "")
            if r.get(f) is None and not raw:
                continue
            if r.get(f) is not None and abs(r[f] - v) <= tol:
                continue
            nv = round(v, nd)
            if len(agree) >= 3 or ot.edit_distance(raw, f"{nv:.{nd}f}") <= 2:
                fixes.append(f"{r['tariff_type']} {r['voltage']} {f}: OCR '{raw}' -> {nv} ({len(agree)} cells agree: {','.join(agree)})")
                r[f] = nv
            else:
                log(f"  !! {r['month']} {r['tariff_type']} {r['voltage']} {f}={r.get(f)} vs derived {nv} – left")
                bad += 1
        # component repair: tdp is the only per-row component
        if "tdp" in comp and None not in [r.get(c) for c in comp]:
            s = sum(r[c] for c in comp)
            if abs(s - e) > tol:
                cand = round(e - (s - r["tdp"]), nd)
                raw = raws[i].get("tdp", "")
                if ot.edit_distance(raw, str(cand)) <= 2 or len(agree) >= 3:
                    fixes.append(f"{r['tariff_type']} {r['voltage']} tdp: OCR '{raw}' -> {cand} (energy − other components)")
                    r["tdp"] = cand
    return bad


def ocr_cache(code, ann, fn, version):
    return ot.cached_parse(ann, code, fn, version=version)


# ------------------------------------------------------------------ generic affine cross-check
def _fmt(v, nd):
    return f"{v:.{nd}f}"


def affine_solve(rows, raws, shared, tou, nd, tol=1.5e-6, log=print, flat_is_energy=True):
    """Cross-validate one month of OCR'd rows with the notice's own arithmetic, without assuming the float
    coefficients:
      * shared components (merged cells: 代理购电价格, 线损, 基金, 系统运行费 …) are equal on every row;
      * energy = Σ shared + tdp (row), flat = energy (columns marked '9=1' / 平段 = 电度电价);
      * each TOU field minus energy is an affine function of the row's tdp (all other float bases are shared),
        fitted robustly over the month's rows (constant when tdp does not float).
    A cell that disagrees is repaired only when the value it must have is pinned by >= 2 independent cells;
    every repair is returned.  Raises RuntimeError when a row cannot be verified."""
    fixes = []
    fix_shared(rows, raws, shared, 1e-9, fixes)
    S_read = sum(rows[0][f] for f in shared) if all(rows[0].get(f) is not None for f in shared) else None
    cands = []
    for i, r in enumerate(rows):
        for f in ("energy_price",) + (("flat",) if flat_is_energy else ()):
            if r.get(f) is not None and r.get("tdp") is not None:
                cands.append((r[f] - r["tdp"], f"{i}{f}"))
    S, src = cluster(cands, tol)
    if S is None or len(src) < 3:
        raise RuntimeError(f"OCR: Σshared not established from energy − tdp ({cands[:6]})")
    if S_read is None or abs(S_read - S) > tol:
        # one shared cell misread / unread: solve it if the result is a small edit of what OCR saw
        done = False
        for f in shared:
            others = sum(rows[0][g] for g in shared if g != f) if all(rows[0].get(g) is not None for g in shared if g != f) else None
            if others is None:
                continue
            cand = round(S - others, nd)
            raw = raws[0].get(f, "")
            if rows[0].get(f) is None or ot.edit_distance(raw, _fmt(cand, nd).rstrip("0")) <= 2 or ot.edit_distance(raw, _fmt(cand, nd)) <= 2:
                fixes.append(f"shared {f}: OCR '{raw}' -> {cand} (energy − tdp − other shared, {len(src)} cells agree)")
                for r in rows:
                    r[f] = cand
                done = True
                break
        if not done:
            raise RuntimeError(f"OCR: Σshared read {S_read} != energy − tdp {S} and no single-cell repair")
    # per row: tdp / energy / flat
    for i, r in enumerate(rows):
        c = []
        if r.get("tdp") is not None:
            c.append((r["tdp"], "tdp"))
        for f in ("energy_price",) + (("flat",) if flat_is_energy else ()):
            if r.get(f) is not None:
                c.append((r[f] - S, f))
        t, ag = cluster(c, tol)
        if t is None or len(ag) < 2:
            raise RuntimeError(f"OCR: row {r['tariff_type']} {r['voltage']}: tdp/energy disagree {c}")
        exp = {"tdp": t, "energy_price": S + t}
        if flat_is_energy:
            exp["flat"] = S + t
        for f, v in exp.items():
            if r.get(f) is None or abs(r[f] - v) > tol:
                ndf = 4 if f == "tdp" else nd
                nv = round(v, ndf)
                fixes.append(f"{r['tariff_type']} {r['voltage']} {f}: OCR '{raws[i].get(f, '')}' -> {nv} ({','.join(ag)} agree)")
                r[f] = nv
    # TOU fields: affine in tdp
    for f in tou:
        pts = [(i, r["tdp"], r[f] - r["energy_price"]) for i, r in enumerate(rows) if r.get(f) is not None]
        have_raw = [i for i, r in enumerate(rows) if r.get(f) is None and re.search(r"\d", raws[i].get(f, "") or "")]
        if len(pts) + len(have_raw) == 0:
            continue
        best = None
        for a in range(len(pts)):
            for b in range(len(pts)):
                if b < a or (b == a and len(pts) > 1):
                    continue
                (_, x1, y1), (_, x2, y2) = pts[a], pts[b]
                if abs(x2 - x1) < 1e-9:
                    if abs(y2 - y1) > tol:
                        continue
                    k, c0 = 0.0, y1
                else:
                    k = (y2 - y1) / (x2 - x1); c0 = y1 - k * x1
                inl = [p for p in pts if abs(p[2] - (c0 + k * p[1])) <= tol]
                key = (len(inl), -abs(k))
                if best is None or key > best[0]:
                    best = (key, k, c0)
        n_in = best[0][0] if best else 0
        if n_in < max(2, len(pts) - 2) or (len(pts) >= 3 and n_in < 3):
            raise RuntimeError(f"OCR: {f} not affine in tdp ({n_in} of {len(pts)} rows agree)")
        _, k, c0 = best
        for i, r in enumerate(rows):
            if r.get(f) is None and i not in have_raw:
                continue
            v = r["energy_price"] + c0 + k * r["tdp"]
            if r.get(f) is None or abs(r[f] - v) > tol:
                nv = round(v, nd)
                fixes.append(f"{r['tariff_type']} {r['voltage']} {f}: OCR '{raws[i].get(f, '')}' -> {nv} "
                             f"(affine fit {f}−energy = {c0:.6f} + {k:.4f}·tdp over {n_in} rows)")
                r[f] = nv
    return fixes


def rows_from_table(tab, columns, layout, month):
    data = tab["data"]
    if len(data) != len(layout):
        raise RuntimeError(f"OCR: {len(data)} data rows, layout expects {len(layout)}")
    rows, raws = [], []
    for (nums, rr), (tt, v) in zip(data, layout):
        r = {f: n for f, n in zip(columns, nums) if f}
        raws.append({f: (s or "") for f, s in zip(columns, rr) if f})
        r.update(month=month, tariff_type=tt, voltage=v, voltage_rank=pc.voltage_rank(v), category="工商业用电")
        rows.append(r)
    return rows, raws


def finish_rows(rows, nd_fields=("sharp", "peak", "flat", "valley", "deep_valley")):
    for r in rows:
        for f in pc.TARIFF_FIELDS:
            r.setdefault(f, None)
        if r["tariff_type"] == "单一制":
            r["demand_price"] = r["capacity_price"] = None
    return rows


def sanity(rows):
    for r in rows:
        for f in ("energy_price", "purchase_price", "tdp", "flat"):
            v = r.get(f)
            if v is None or not (0 < v < 3):
                raise RuntimeError(f"OCR: implausible {f}={v} for {r['tariff_type']} {r['voltage']}")
        for f in ("demand_price", "capacity_price"):
            v = r.get(f)
            if v is not None and not (5 < v < 80):
                raise RuntimeError(f"OCR: implausible {f}={v} for {r['tariff_type']} {r['voltage']}")
    bad = [x for x in pc.validate_rows(rows) if x["check"] in ("components_sum", "components_missing", "tou_order")]
    if bad:
        raise RuntimeError(f"OCR unreliable: {len(bad)} failures, e.g. {bad[0]}")
    for tt in ("单一制", "两部制"):
        g = sorted([r for r in rows if r["tariff_type"] == tt], key=lambda r: r["voltage_rank"])
        for lo, hi in zip(g, g[1:]):
            if not hi["tdp"] < lo["tdp"]:
                raise RuntimeError(f"OCR/layout: tdp not decreasing {tt} {lo['voltage']}={lo['tdp']} {hi['voltage']}={hi['tdp']}")


def regulated_postprocess(parsed, anns, period_key, fields=("tdp", "demand_price", "capacity_price")):
    """ocr_table.regulated_consensus (majority within a TDP period replaces a disagreeing read), then 两部制 需量/容量
    cells OCR left blank are filled with the period's majority value (>= 2 months agree). All changes logged."""
    from collections import Counter
    log = ot.regulated_consensus(parsed, anns, fields=fields, period_key=period_key)
    groups = {}
    for m, rows in parsed.items():
        for r in rows:
            if r["tariff_type"] == "两部制":
                for f in ("demand_price", "capacity_price"):
                    groups.setdefault((period_key(m, rows), r["voltage"], f), []).append((m, r))
    for (pk, v, f), items in groups.items():
        cnt = Counter(r[f] for _, r in items if r.get(f) is not None)
        if not cnt:
            continue
        val, n = cnt.most_common(1)[0]
        if n < 2 or n <= sum(cnt.values()) / 2:
            continue
        for m, r in items:
            if r.get(f) is None:
                msg = f"两部制 {v} {f}: blank in OCR -> {val} (regulated, {n} months of the period agree)"
                anns[m].setdefault("ocr_corrections", []).append(msg)
                log.append(f"{m} {msg}")
                r[f] = val
    return log
