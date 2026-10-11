"""Shared helpers for the north-west group (陕西 / 陕西榆林 / 甘肃 / 青海 / 新疆 / 西藏).

All provincial sgcc hosts of this group are unreachable from the pipeline network, so every monthly notice comes
from State Grid's national portal 95598.cn (网上国网 › 信息公开 › 执行的电价和收费标准, partNo=P2019, region
selector).  The listing is JS-only, so the article / omg-static URLs were collected once with the Hark browser into
``data/raw/<code>/seed.json``; the omg-static files themselves are static and curl-downloadable.  omg-static URLs
carry no file extension: ``seed.json`` records ``file_ext`` (pdf / png / jpg) as detected after download.

Contract helpers (bound by each region module):
  seed_discover(code, start, end)       -> announcement dicts (one per month, from seed.json)
  download(code, ann)                   -> data/raw/<code>/YYYY-MM.<ext>, magic-checked, sha256
  policy_doc_row(code, ann, id_salt)    -> policy_doc row (id_salt keeps two regions that share one file apart)
  text_table_rows(...)                  -> rows from a text PDF page table (pdfplumber + sgcc_cms.parse_grid)
"""
import json, os, re

from . import sgcc_cms as cms
from .. import parse_common as pc

MAGIC = {"pdf": [b"%PDF"], "png": [b"\x89PNG"], "jpg": [b"\xff\xd8"], "jpeg": [b"\xff\xd8"],
         "docx": [b"PK"], "xls": [b"\xd0\xcf\x11\xe0"], "xlsx": [b"PK"]}
SHARED = ("purchase_price", "line_loss", "system_op", "gov_fund")


def raw_dir(code):
    return os.path.join(pc.DATA, "raw", code)


def seed_discover(code, start, end, log=print):
    wanted = set(cms.months_between(start, end))
    p = os.path.join(raw_dir(code), "seed.json")
    seed = json.load(open(p, encoding="utf-8"))
    out = []
    for a in seed["articles"]:
        if a["month"] not in wanted:
            continue
        ann = {k: a.get(k) for k in ("month", "title", "article_url", "publish_date", "file_url", "file_ext")}
        ann["discovered_via"] = "seed (95598.cn P2019, collected with browser)"
        out.append(ann)
        log(f"  {a['month']}: {a['file_url']} ({a['file_ext']})")
    return sorted(out, key=lambda x: x["month"])


def download(code, ann, log=print):
    d = raw_dir(code)
    os.makedirs(d, exist_ok=True)
    ext = ann["file_ext"]
    path = os.path.join(d, f"{ann['month']}.{ext}")
    if not (os.path.exists(path) and os.path.getsize(path) > 1000):
        r = pc.http_get(ann["file_url"], timeout=90)
        if not any(r.content.startswith(m) for m in MAGIC.get(ext, [b""])):
            raise RuntimeError(f"unexpected file signature for {ann['file_url']}: {r.content[:8]!r}")
        with open(path, "wb") as f:
            f.write(r.content)
        log(f"  downloaded {os.path.relpath(path, pc.ROOT)} ({len(r.content)} bytes)")
    ann["file_path"] = path
    ann["sha256"] = pc.sha256_file(path)
    return path


def policy_doc_row(code, ann, id_salt=""):
    return {
        "id": pc.doc_id(ann["file_url"] + id_salt), "region_code": code, "kind": "agency_price",
        "title": ann["title"], "doc_no": None, "publish_date": ann.get("publish_date"),
        "effective_from": ann.get("effective_from") or f"{ann['month']}-01", "effective_to": ann.get("effective_to"),
        "source_url": ann["file_url"], "file_path": os.path.relpath(ann["file_path"], pc.ROOT),
        "sha256": ann["sha256"], "fetched_at": pc.now_iso(),
    }


def tou_doc(code, url, title, doc_no, publish_date, effective_from, effective_to=None, id_salt=""):
    d = cms.tou_doc(code, url, title, doc_no, publish_date, effective_from, effective_to)
    d["id"] = pc.doc_id(url + id_salt)
    return d


def fill_shared(rows, fields=SHARED):
    """pdfplumber puts a vertically merged cell's value into ONE row of the span (not always the first).  The
    purchase / line-loss / system-op / fund columns are one merged cell for the whole table, so every row gets the
    single value found; more than one distinct value is an error (the table is not what we think)."""
    for f in fields:
        vals = {r[f] for r in rows if r.get(f) is not None}
        if len(vals) > 1:
            raise RuntimeError(f"merged column {f} has several values {sorted(vals)}")
        if vals:
            v = vals.pop()
            for r in rows:
                r[f] = v
    return rows


def find_page_table(path, want_res, avoid_res=(r"1\.5倍",), head_len=120):
    """First pdfplumber page whose text matches all want_res and none of avoid_res → (page_text, grid)."""
    for i, text, tables in cms.pdf_tables(path):
        t = re.sub(r"\s+", "", text)
        head = t[:head_len]
        if all(re.search(w, head) for w in want_res) and not any(re.search(a, head) for a in avoid_res):
            if not tables:
                raise RuntimeError(f"page {i + 1} matches {want_res} but has no table")
            grid = max(tables, key=len)
            return i, text, grid
    raise RuntimeError(f"no page matching {want_res}")


def check_month(text, month):
    """执行时间 printed on the page must be the announcement month (YYYY年M月)."""
    t = re.sub(r"\s+", "", text)
    m = re.search(r"执行时间[：:]?(\d{4})年(\d{1,2})月", t)
    if not m:
        raise RuntimeError("执行时间 not found on page")
    got = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
    if got != month:
        raise RuntimeError(f"执行时间 {got} != announcement month {month}")


def zero_to_none(rows, fields=("sharp", "deep_valley", "demand_price", "capacity_price")):
    """Some tables print 0 in a column that does not apply that month (e.g. 陕西 尖峰 outside 1、7、8、12月)."""
    for r in rows:
        for f in fields:
            if r.get(f) == 0:
                r[f] = None
    return rows


def ocr_grid(img, table, a, ncols, ref_col, n_label=3, n_head_lines=4, skip_cols=(), scale=1.5):
    """Ruled-table reader (ocr_table primitives) for posters whose shared columns are ONE merged cell over all data
    rows.  Row rules are taken from column ``ref_col`` (a TOU-price column ruled on every row); a numeric column
    without inner row rules is read once over the whole data band.  ``skip_cols`` are not OCR'd here (e.g. a merged
    multi-line cell read separately).  Returns (vals, raws, merged_cols, bands, num_cols)."""
    import numpy as np
    from . import ocr_table as ot
    xs = ot.vlines(a, table)
    cols = [(x0, x1) for x0, x1 in zip(xs, xs[1:]) if x1 - x0 > 8]
    num = cols[n_label:]
    if len(num) != ncols:
        raise RuntimeError(f"OCR: {len(num)} numeric columns, expected {ncols}")
    rx = num[ref_col]
    ys = [y for y in table["hlines"] if ot._has_rule(a, y, *rx)]
    bands = list(zip(ys, ys[1:]))[n_head_lines - 1:]
    bands = [b for b in bands if b[1] - b[0] > 12]
    vals = [[None] * ncols for _ in bands]
    raws = [[""] * ncols for _ in bands]
    merged = []
    for c, (x0, x1) in enumerate(num):
        if c in skip_cols:
            continue
        if not any(ot._has_rule(a, b[0], x0, x1) for b in bands[1:]):
            merged.append(c)
            y0, y1 = bands[0][0], bands[-1][1]
            sub = a[y0 + 3:y1 - 2, x0 + 3:x1 - 2]
            ink = np.where(sub.mean(axis=1) > 0.01)[0]
            v, s = (None, "")
            if len(ink):
                v, s = ot.ocr_num(img, (x0, y0 + 3 + ink.min() - 4, x1, y0 + 3 + ink.max() + 5), scale)
            for r in range(len(bands)):
                vals[r][c], raws[r][c] = v, s
        else:
            for r, (y0, y1) in enumerate(bands):
                vals[r][c], raws[r][c] = ot.ocr_num(img, (x0, y0, x1, y1), scale)
    return vals, raws, merged, bands, num
