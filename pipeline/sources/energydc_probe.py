"""energydc.cn prototype adapter (PROBE ONLY – not wired into run.py).

What it does
  discover(region, start, end) -> [{month, uuid, url}]   from the PUBLIC sitemap
                                   https://energydc.cn/sitemap/sitemap_policy_1.xml (1 request, cached)
  fetch_raw(url)                -> plain-HTTP fetch; energydc.cn is a JS SPA, so this almost always
                                   returns the 4.7 KB app shell ("stub") -> raises StubError.
  parse_markdown(md, month)     -> tariff_month rows (pipeline/schema.sql) from a RENDERED page saved
                                   as markdown (e.g. Hark web_fetch output) at
                                   data/raw/energydc/<region>/<YYYY-MM>_<uuid>.md

Constraints honoured: only public pages (policy pages + sitemap, robots.txt = "Allow: /"),
no login, no back-end/API endpoints, >=2 s between requests, <=2 retries, no bulk crawl.
ToS §4 (energydc.cn/terms, 2025-12-10) forbids unauthorised use/copying/redistribution of site
content – treat parsed numbers as a cross-check, keep official PDFs as the source of record.

CLI
  python -m pipeline.sources.energydc_probe discover --region shanghai --from 2025-11 --to 2026-10
  python -m pipeline.sources.energydc_probe parse --md data/raw/energydc/shanghai/2025-11_<uuid>.md --month 2025-11
"""
import argparse, json, os, re, sys, time

from .. import parse_common as pc

BASE = "https://energydc.cn"
SITEMAP = BASE + "/sitemap/sitemap_policy_1.xml"
RAW_DIR = os.path.join(pc.DATA, "raw", "energydc")
MIN_INTERVAL = 2.0
_last = [0.0]


class StubError(RuntimeError):
    pass


def _polite_get(url):
    wait = MIN_INTERVAL - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    try:
        return pc.http_get(url, retries=2).content  # http_get never retries more than twice
    finally:
        _last[0] = time.time()


def _months(start, end):
    y, m = map(int, start.split("-")); ey, em = map(int, end.split("-"))
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def discover(region, start, end):
    os.makedirs(RAW_DIR, exist_ok=True)
    cache = os.path.join(RAW_DIR, "sitemap_policy_1.xml")
    if not os.path.exists(cache) or time.time() - os.path.getmtime(cache) > 86400:
        open(cache, "wb").write(_polite_get(SITEMAP))
    xml = open(cache, encoding="utf-8").read()
    want = set(_months(start, end))
    out = []
    for month, uuid in re.findall(rf"<loc>{re.escape(BASE)}/policy/{re.escape(region)}/(\d{{4}}-\d{{2}})/([0-9a-f-]{{36}})</loc>", xml):
        if month in want:
            out.append({"month": month, "uuid": uuid, "url": f"{BASE}/policy/{region}/{month}/{uuid}"})
    return sorted(out, key=lambda x: x["month"])


def fetch_raw(url):
    """Plain HTTP. Returns HTML only if it already contains a data table; otherwise StubError.
    (Observed 2026-10-11: every curl fetch returned the SPA shell; content needs JS rendering.)"""
    html = _polite_get(url).decode("utf-8", "ignore")
    if "<table" not in html:
        raise StubError(f"SPA shell only ({len(html)} bytes) – render with a JS-capable fetcher: {url}")
    return html


# ---------------- markdown parsing ----------------
_SKIP_TITLE = re.compile(r"1\.5\s*倍|充换电|代理购电价格信息表")
_HDR_FIRST = ("用电分类", "公式", "计算关系")
COLMAP = [  # order matters: first match wins
    ("line_loss", r"线损"),
    ("purchase_price", r"购电价"),
    ("tdp", r"输配"),
    ("system_op", r"系统运行"),
    ("gov_fund", r"基金"),
    ("sharp", r"尖峰"),
    ("peak", r"高峰|峰时段$|峰段"),
    ("flat", r"平时|平段"),
    ("deep_valley", r"深谷"),
    ("valley", r"低谷|谷段"),
    ("demand_price", r"需量"),
    ("capacity_price", r"变压器容量|容量"),
    ("energy_price", r"非分时电度|电度用电价格|电度电价|1=2"),
]


def _cells(line):
    line = line.strip().strip("|")
    return [c.strip().replace("\\.", ".").replace("\\-", "-") for c in line.split("|")]


def _blocks(md):
    """Yield (context_lines, rows) for each markdown table."""
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith("|"):
            j = i
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                j += 1
            ctx = [l for l in lines[max(0, i - 6):i] if l.strip()]
            rows = [_cells(l) for l in lines[i:j] if not re.fullmatch(r"\|?\s*(-{3,}\s*\|\s*)+-*\s*\|?", l.strip())]
            yield ctx, rows
            i = j
        else:
            i += 1


def _norm_voltage(v):
    v = pc.norm(v)
    v = re.sub(r"^1[~～-]10\(20\)千?$", "1-10(20)千伏", v)          # truncated "1-10（20）千"
    v = re.sub(r"^1[~～]10", "1-10", v)
    v = re.sub(r"千伏及$", "千伏及以上", v)                          # truncated "220千伏及"
    return v


def _rank(v):
    # pc.voltage_rank misses bare "10千伏"/"20千伏" (Shanghai/Zhejiang wording) – patch locally
    return pc.voltage_rank(v) or (2 if re.fullmatch(r"(10|20)千伏", pc.norm(v)) else None)


def _classify(desc):
    """desc cells left of the energy column -> (category, tariff_type, voltage)."""
    cat = tariff = volt = None
    sub = []
    for c in desc:
        c0 = pc.norm(c)
        if not c0 or c0 in ("a", "-"):
            continue
        if c0 in ("单一制", "两部制"):
            tariff = c0
        elif "千伏安" in c0:
            sub.append(c0)
        elif "千伏" in c0 or re.fullmatch(r"[\d~-]+(\(20\))?千", c0):
            volt = _norm_voltage(c0)
        else:
            cat = c0
    if sub and cat:
        cat = f"{cat}({''.join(sub)})"       # same convention as pipeline/sources/jiangsu.py
    return cat, tariff, volt


def parse_markdown(md, month):
    rows = []
    for ctx, tbl in _blocks(md):
        if any(_SKIP_TITLE.search(c) for c in ctx[-4:]):
            continue
        hdr = [r for r in tbl if r and pc.norm(r[0]) in _HDR_FIRST]
        data = [r for r in tbl if r and pc.norm(r[0]) not in _HDR_FIRST]
        if not hdr or not data:
            continue
        ncol = max(len(r) for r in tbl)
        labels = []
        for k in range(ncol):
            labels.append("".join(pc.norm(h[k]) for h in hdr if k < len(h) and pc.norm(h[k]) not in ("a",)))
        colfield = {}
        for k, lab in enumerate(labels):
            for field, pat in COLMAP:
                if re.search(pat, lab) and field not in colfield.values():
                    colfield[k] = field
                    break
        if "energy_price" not in colfield.values():
            continue
        e_idx = min(k for k, f in colfield.items() if f == "energy_price")
        for r in data:
            cat, tariff, volt = _classify(r[:e_idx])
            if not (cat and tariff and volt):
                continue
            row = {f: None for f in pc.TARIFF_FIELDS}
            row.update(month=month, category=cat, tariff_type=tariff, voltage=volt,
                       voltage_rank=_rank(volt))
            for k, f in colfield.items():
                if k < len(r) and k >= e_idx:
                    row[f] = pc.num(r[k])
            if tariff == "单一制":
                # energydc's table extraction propagates merged 容(需)量 cells into 单一制 rows
                # (seen jiangsu 2026-04); single-part tariffs carry no demand/capacity charge.
                row["demand_price"] = row["capacity_price"] = None
            rows.append(row)
        if rows:
            break  # first qualifying table = 代理购电工商业用户电价表 (main table)
    return rows


def doc_meta(md, url):
    t = re.search(r"^#\s+(.+)$", md, re.M)
    def _d(label):
        m = re.search(label + r"\s*(\d{4})年(\d{1,2})月(\d{1,2})日", md)
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None
    return {"id": pc.doc_id(url), "kind": "agency_price", "title": t.group(1).strip() if t else None,
            "effective_from": _d("生效日期"), "effective_to": _d("失效日期"), "source_url": url,
            "fetched_at": pc.now_iso()}


def main(argv=None):
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    d = sp.add_parser("discover"); d.add_argument("--region", required=True)
    d.add_argument("--from", dest="start", required=True); d.add_argument("--to", dest="end", required=True)
    p = sp.add_parser("parse"); p.add_argument("--md", required=True); p.add_argument("--month", required=True)
    p.add_argument("--url", default="")
    a = ap.parse_args(argv)
    if a.cmd == "discover":
        print(json.dumps(discover(a.region, a.start, a.end), ensure_ascii=False, indent=1))
    else:
        md = open(a.md, encoding="utf-8").read()
        rows = parse_markdown(md, a.month)
        fails = pc.validate_rows(rows)
        print(json.dumps({"doc": doc_meta(md, a.url or a.md), "n_rows": len(rows), "rows": rows,
                          "validation_failures": fails}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.exit(main())
