"""energydc.cn PROBE via the JSON endpoint behind the public /policy/<region>/<YYYY-MM>/<uuid> page.

Not wired into run.py. One region-month per invocation; 1 request / 2 s; <=2 retries.

Sources (all publicly reachable without login, 2026-10-11):
  sitemap   https://energydc.cn/sitemap/sitemap_policy_1.xml      -> region/month/uuid for every policy page
  detail    https://energydc.cn/edc/api/v1/eprice/policy/<uuid>    -> JSON {title, content(markdown: announcement
            text + parsed 电价表 as markdown tables), effective, expired, regionId, typeId, file(.webp scan)}
            This is the same XHR the public policy page makes for a guest (no Authorization header).
  NOT used: /edc/api/v1/eprice/price-list (structured TOU stage JSON) - the site's UI limits guests to the
            current month for /query, so past months there are treated as gated and not requested.

ToS (energydc.cn/terms, 2025-12-10, §4): all content is IP-protected; "未经授权，任何单位或个人不得擅自使用、复制、
修改或传播". The site also sells per-province full CSVs (¥29.9). Treat output as a cross-check only; the
official grid announcement stays the source of record. Do not bulk-crawl.

Usage:
  python -m pipeline.sources.energydc_api_probe --region jiangsu --month 2025-11 [--out work/edc_jiangsu_2025-11.json]
Output: JSON {"policy": {...policy_doc fields}, "tariff_month": [...rows per schema.sql], "tou_rule": [...best-effort]}
"""
import argparse, json, os, re, sys, time
from datetime import datetime, timezone, timedelta

from .. import parse_common as pc

BASE = "https://energydc.cn"
SITEMAP = BASE + "/sitemap/sitemap_policy_1.xml"
API = BASE + "/edc/api/v1/eprice/policy/"
CACHE = os.path.join(pc.DATA, "raw", "energydc")
MIN_INTERVAL = 2.0
_last = [0.0]
CST = timezone(timedelta(hours=8))


def _get(url):
    wait = MIN_INTERVAL - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    try:
        return pc.http_get(url, retries=2).content
    finally:
        _last[0] = time.time()


def find_uuids(region, month):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, "sitemap_policy_1.xml")
    if not os.path.exists(path) or time.time() - os.path.getmtime(path) > 86400:
        open(path, "wb").write(_get(SITEMAP))
    xml = open(path, encoding="utf-8").read()
    return re.findall(rf"/policy/{re.escape(region)}/{month}/([0-9a-f-]{{36}})", xml)


def fetch_policy(uuid):
    path = os.path.join(CACHE, f"policy_{uuid}.json")
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    d = json.loads(_get(API + uuid))
    if d.get("code") != 200 or not d.get("data"):
        raise RuntimeError(f"policy {uuid}: {d.get('code')} {d.get('message')}")
    json.dump(d["data"], open(path, "w", encoding="utf-8"), ensure_ascii=False)
    return d["data"]


# ---------- markdown table parsing (header-driven; column order differs per province) ----------
COLMAP = [  # (field, include-regex, exclude-regex) - first match wins, order matters
    ("tdp", r"输配", None),
    ("gov_fund", r"政府性基金", None),
    ("line_loss", r"线损|上网环节", None),
    ("system_op", r"系统运行", None),
    ("purchase_price", r"购电价格", None),
    ("sharp", r"尖峰", None),
    ("deep_valley", r"深谷", None),
    ("peak", r"高峰|峰时段|^峰", None),
    ("valley", r"低谷|谷时段|^谷", None),
    ("flat", r"平时|平段", None),
    ("demand_price", r"需量", None),
    ("capacity_price", r"容量", r"补偿"),
    ("energy_price", r"电度|电量|用电价格", r"(?<!非)分时"),
]
SUM_FIELDS = {"purchase_price", "system_op"}  # e.g. Shandong splits these into sub-columns
ENERGY_FIELDS = ["energy_price", "purchase_price", "line_loss", "tdp", "system_op", "gov_fund",
                 "sharp", "peak", "flat", "valley", "deep_valley"]
VOLT_RE = re.compile(r"(千伏(?!安)|kV|KV|伏$)")


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _tables(md):
    """-> [(caption, rows)]; caption = nearest non-empty text line above the table (e.g. '### ...电价表',
    '执行时间...', or a sub-region / 1.5倍 heading) so callers can tell parallel tables apart."""
    out, cur, prev, cap = [], [], [], ""
    for ln in md.splitlines():
        if ln.lstrip().startswith("|"):
            if not cur:
                cap = " / ".join(prev[-2:])
            cur.append(_cells(ln))
        else:
            if cur:
                out.append((cap, cur)); cur = []
            if ln.strip():
                prev.append(ln.strip()[:120])
    if cur:
        out.append((cap, cur))
    return out


def _is_header(row):
    h = pc.norm(row[0])
    return h in ("用电分类", "计算关系", "公式", "") or set("".join(row)) <= set("-: ")


def parse_table(rows):
    hdr = [r for r in rows if _is_header(r)]
    data = [r for r in rows if not _is_header(r)]
    if not hdr or not data:
        return []
    ncol = max(len(r) for r in rows)
    merged = []
    for j in range(ncol):
        parts = []
        for r in hdr:
            if pc.norm(r[0]) in ("计算关系", "公式"):
                continue
            c = r[j] if j < len(r) else ""
            if c and c != "nan" and set(c) - set("-: ") and c not in parts:
                parts.append(c)
        merged.append(pc.norm("".join(parts)))
    alltext = "".join(merged)
    if not re.search(r"电压等级", alltext) or not re.search(r"谷", alltext):
        return []  # not a TOU price table (e.g. 附表1 购电价格信息表)
    in_fen = "分/千瓦时" in alltext
    vcol = next(j for j, h in enumerate(merged) if "电压等级" in h)
    fieldcols = {}
    # columns are "seen through" their parent group header (其中/分时电度电价...), so match on the
    # merged text but strip generic group words first
    for j, h in enumerate(merged):
        if j <= vcol:
            continue
        hh = re.sub(r"其中|(?<!非)分时电[度量]用?电价(格)?|容\(需\)量用?电价(格)?|\(.*?\)|（.*?）", "", h) or h
        for f, inc, exc in COLMAP:
            if re.search(inc, hh) and not (exc and re.search(exc, hh)):
                fieldcols.setdefault(f, []).append(j)
                break
    out = []
    for r in data:
        r = [("" if c == "nan" else c) for c in r] + [""] * (ncol - len(r))
        label = [c for c in r[:vcol + 1] if c]
        volt = r[vcol] if VOLT_RE.search(pc.norm(r[vcol])) else next(
            (c for c in label if VOLT_RE.search(pc.norm(c))), None)
        if not volt:
            continue
        joined = "".join(label)
        tariff = "两部制" if "两部制" in joined else ("单一制" if "单一制" in joined else None)
        cat = next((c for c in label if "用电" in c or "工业" in c or "工商业" in c), None)
        cat = re.sub(r"[-－]?(单一制|两部制)", "", cat or "").strip() or None
        sub = next((c for c in label if "千伏安" in c), None)  # e.g. Jiangsu 100千伏安及以上/以下
        row = {"category": cat, "tariff_type": tariff,
               "voltage": volt + (f"[{sub}]" if sub else ""), "voltage_rank": pc.voltage_rank(volt)}
        for f, cols in fieldcols.items():
            vals = [pc.num(r[j].replace("\\", "")) for j in cols]
            vals = [v for v in vals if v is not None]
            v = (sum(vals) if f in SUM_FIELDS else vals[0]) if vals else None
            if v is not None and in_fen and f in ENERGY_FIELDS:
                v = v / 100.0
            row[f] = round(v, 8) if v is not None else None
        out.append(row)
    return out


# ---------- TOU periods from the announcement notes (best effort, free text) ----------
SEG_RE = re.compile(r"(尖峰|高峰|平|低谷|深谷)时段(?:为)?[：:]?\s*((?:(?:次日)?\d{1,2}:\d{2}\s*[-—~至]\s*(?:次日)?\d{1,2}:\d{2}[、，, ]*)+)")
KIND = {"尖峰": "sharp", "高峰": "peak", "平": "flat", "低谷": "valley", "深谷": "deep_valley"}


def _months_of(text):
    ms = []
    for a, b in re.findall(r"(\d{1,2})\s*月?\s*[-—至~]\s*(?:次年)?\s*(\d{1,2})\s*月", text):
        a, b = int(a), int(b)
        ms += list(range(a, b + 1)) if a <= b else list(range(a, 13)) + list(range(1, b + 1))
    return sorted(set(m for m in ms if 1 <= m <= 12))


def parse_tou(content):
    text = re.sub(r"\s+", "", content)
    rules = []
    # split into clauses that start with a season/month phrase (e.g. 夏、冬两季（每年6-8月、12月-次年2月）时段设置：)
    for m in re.finditer(r"([^。；]*?(?:\d{1,2}月[^。；]{0,40}?)时段(?:设置|划分)?[:：])([^。]*)", text):
        months = _months_of(m.group(1))
        segs = []
        for k, spans in SEG_RE.findall(m.group(2)):
            for a, b in re.findall(r"((?:次日)?\d{1,2}:\d{2})\s*[-—~至]\s*((?:次日)?\d{1,2}:\d{2})", spans):
                segs.append([a.replace("次日", ""), b.replace("次日", ""), KIND[k]])
        if months and segs:
            rules.append({"months": months, "day_type": "normal", "segments": sorted(segs, key=lambda x: int(x[0].split(":")[0]) * 60 + int(x[0].split(":")[1]))})
    return rules


def run(region, month):
    uuids = find_uuids(region, month)
    if not uuids:
        raise SystemExit(f"no public policy page for {region} {month} in sitemap")
    for u in uuids:
        d = fetch_policy(u)
        if d.get("typeId") != 2:  # 2 = 代理购电公告; 1 = 分时电价解读/政策
            continue
        url = f"{BASE}/policy/{region}/{month}/{u}"
        did = pc.doc_id(url)
        rows = []
        for ti, (cap, t) in enumerate(_tables(d.get("content", ""))):
            for r in parse_table(t):
                # table_idx/table_caption are NOT schema columns: they disambiguate parallel tables
                # (Guangdong sub-regions, 1.5x-price tables) that would otherwise collide on the PK.
                r.update({"region_code": region, "month": month, "doc_id": did,
                          "table_idx": ti, "table_caption": cap})
                rows.append(r)
        eff = lambda ts: datetime.fromtimestamp(ts, CST).strftime("%Y-%m-%d") if ts else None
        tou = [dict(r, region_code=region, effective_from=eff(d.get("effective")),
                    effective_to=eff(d.get("expired")), months=json.dumps(r["months"]),
                    segments=json.dumps(r["segments"]), doc_id=did) for r in parse_tou(d.get("content", ""))]
        return {"policy": {"id": did, "region_code": region, "kind": "agency_price", "title": d.get("title"),
                           "effective_from": eff(d.get("effective")), "effective_to": eff(d.get("expired")),
                           "source_url": url, "api_url": API + u, "scan_file": d.get("file"),
                           "fetched_at": pc.now_iso()},
                "tariff_month": rows, "tou_rule": tou}
    raise SystemExit(f"{region} {month}: pages found but none is a 代理购电公告 (typeId 2)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", required=True)
    ap.add_argument("--month", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    res = run(a.region, a.month)
    s = json.dumps(res, ensure_ascii=False, indent=1)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(s)
    print(s if not a.out else f"wrote {a.out}: {len(res['tariff_month'])} tariff rows, {len(res['tou_rule'])} tou rules")
