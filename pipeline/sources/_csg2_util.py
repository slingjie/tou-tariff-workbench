"""Shared helpers for the csg2 group (南方电网: 广西 guangxi, 云南 yunnan, 贵州 guizhou, 海南 hainan).

Source (official, free, no login): 南方电网 95598 "南网在线" information API (same as _guangdong_util):
    POST https://95598.csg.cn/ucs/ma/wt/searchService/queryInformationList
    {"areaCode": "040000"|"050000"|"060000"|"070000", "level": "2", "classId": "", "keyword": "代理购电",
     "pageNo": n, "pageSize": 50, "version": "cn"}
Each notice is filed under 1–4 columns (classId) with the same attachment; one is kept (preferred classId, else first
by link).  `link` is either a static page /ucs/sr/info/<prov>/<classId>/<ts>/<infoId>.html holding a
downloadFiles?documentId=… PDF link (+ JPG/PNG previews), or the downloadFiles PDF itself.
PDFs are saved to data/raw/<code>/YYYY-MM.pdf (curl, 1 try + max 2 retries).
"""
import calendar, json, os, re

from . import _guangdong_util as G
from .. import parse_common as pc

API = G.API


def api_list(area_code, keyword="代理购电", max_pages=10):
    items = []
    for page in range(1, max_pages + 1):
        body = json.dumps({"areaCode": area_code, "level": "2", "classId": "", "pageNo": page, "pageSize": 50,
                           "version": "cn", "keyword": keyword}, ensure_ascii=False)
        raw = G._curl(["-H", "Content-Type: application/json;charset=UTF-8", "-H", "Origin: https://95598.csg.cn",
                       "-H", "Referer: https://95598.csg.cn/", "-X", "POST", API, "--data-binary", body], timeout=40)
        lst = (json.loads(raw).get("data") or {}).get("infoList") or []
        if not lst:
            break
        items += [x for x in lst if "infoTitle" in x]
        if len(lst) < 50:
            break
    return items


def discover(area_code, title_re, start, end, pref_class=(), log=print):
    """title_re: compiled regex with groups (year, month)."""
    wanted = set(G.months_between(start, end))
    by_month = {}
    for it in api_list(area_code):
        m = title_re.search(it["infoTitle"].strip())
        if not m:
            continue
        month = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
        if month in wanted:
            by_month.setdefault(month, []).append(it)

    def rank(x):
        for i, p in enumerate(pref_class):
            if x["classId"].startswith(p):
                return i
        return 99
    anns = []
    for month in sorted(by_month):
        ann = None
        for it in sorted(by_month[month], key=lambda x: (rank(x), x["link"])):   # same notice under several columns
            cand = {"month": month, "title": it["infoTitle"].strip(),
                    "publish_date": (it.get("publishTime") or "")[:10] or None,
                    "info_id": it.get("infoId"), "class_id": it.get("classId"), "file_ext": "pdf"}
            if "downloadFiles" in it["link"]:
                cand["article_url"], cand["file_url"] = None, it["link"]
                ann = cand
                break
            html = G._curl([it["link"]], timeout=40).decode("utf-8", "ignore")
            pdfs = re.findall(r'href="(https://95598\.csg\.cn/ucs/ma/wt/business/downloadFiles\?[^"]+)"', html)
            if pdfs:
                cand["article_url"], cand["file_url"] = it["link"], pdfs[0].replace("&amp;", "&")
                ann = cand
                break
            log(f"  {month}: no PDF link in {it['link']} – trying the next column")
        if ann is None:
            log(f"  !! {month}: no PDF in any column")
            continue
        anns.append(ann)
    for m in sorted(wanted - set(by_month)):
        log(f"  {m}: no notice in 95598.csg.cn API (areaCode {area_code})")
    return anns


def download(code, ann, log=print):
    raw_dir = os.path.join(pc.DATA, "raw", code)
    os.makedirs(raw_dir, exist_ok=True)
    path = os.path.join(raw_dir, f"{ann['month']}.pdf")
    if not (os.path.exists(path) and os.path.getsize(path) > 1000):
        data = G._curl([ann["file_url"]], timeout=90)
        if not data.startswith(b"%PDF"):
            raise RuntimeError(f"not a PDF: {ann['file_url']}")
        open(path, "wb").write(data)
        log(f"  downloaded {code} {ann['month']} ({len(data)} B)")
    ann["file_path"] = os.path.relpath(path, pc.ROOT)
    ann["sha256"] = pc.sha256_file(path)
    return ann


def policy_doc_row(code, ann):
    url = ann["file_url"]
    y, m = map(int, ann["month"].split("-"))
    return {"id": pc.doc_id(url + "#" + code), "region_code": code, "kind": "agency_price", "title": ann["title"],
            "doc_no": None, "publish_date": ann.get("publish_date"),
            "effective_from": f"{y:04d}-{m:02d}-01",
            "effective_to": f"{y:04d}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}",
            "source_url": url, "file_path": ann.get("file_path"), "sha256": ann.get("sha256"),
            "fetched_at": pc.now_iso()}


EXEC_RE = re.compile(r"执行时间[:：]\s*(\d{4})\s*年\s*(\d{1,2})\s*月")


def exec_month(text):
    m = EXEC_RE.search(re.sub(r"\s+", "", text))
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}" if m else None


NUM = re.compile(r"^-?\d+(?:\.\d+)?$")


def tou_check(rows, expected_fn, tol=1.5e-6, label="tou_formula"):
    """expected_fn(row) -> {field: value}; compare against printed values (only where printed)."""
    fails = []
    for r in rows:
        for k, v in (expected_fn(r) or {}).items():
            if r.get(k) is not None and v is not None and abs(r[k] - v) > tol:
                fails.append({"month": r["month"], "category": r["category"], "tariff_type": r["tariff_type"],
                              "voltage": r["voltage"], "check": f"{label}_{k}",
                              "detail": f"{k}={r[k]} expected={round(v, 6)}"})
    return fails


# ------------------------------------------------------------------ generic text-PDF table parser
import pdfplumber

HEADER_KEYS = [  # (substring in normalized header text of a column, field) – first match wins per column
    ("代理购电价格", "purchase_price"), ("购电价格", "purchase_price"), ("输配", "tdp"), ("线损", "line_loss"), ("系统运行", "system_op"), ("基金", "gov_fund"), ("尖峰", "sharp"), ("高峰", "peak"),
    ("平时段", "flat"), ("平段", "flat"), ("低谷", "valley"), ("深谷", "deep_valley"), ("需量", "demand_price"),
    ("容量", "capacity_price"), ("电量电价", "energy_price"), ("电度用电价格", "energy_price"),
    ("电度电价", "energy_price"), ("用户电价", "energy_price"),
]
SHARED_FIELDS = ("purchase_price", "line_loss", "system_op", "gov_fund")   # may be merged cells spanning rows


def _cellnum(s):
    if s is None:
        return None
    s = re.sub(r"\s+", "", str(s))
    return float(s) if NUM.match(s) else None


def map_header(tbl, header_rows):
    cols = {}
    width = max(len(r) for r in tbl)
    for c in range(width):
        txt = "".join(re.sub(r"\s+", "", r[c]) for r in tbl[:header_rows] if c < len(r) and r[c])
        if "其中" == txt:
            continue
        for key, field in HEADER_KEYS:
            if key in txt and field not in cols.values():
                cols[c] = field
                break
    return cols


def find_table(path, want, avoid=("1.5倍", "1.5 倍")):
    """(table, page_text) of the first page whose text contains `want` and none of `avoid` (checked on the title
    lines before the table)."""
    with pdfplumber.open(path) as pdf:
        for pg in pdf.pages:
            text = pg.extract_text() or ""
            head = re.sub(r"\s+", "", text[:300])
            if want in head and not any(a.replace(" ", "") in head for a in avoid):
                tbls = [t for t in pg.extract_tables() if t and len(t) > 4]
                if tbls:
                    return tbls[0], text
    raise RuntimeError(f"no table titled '{want}' in {path}")


def rows_from_table(tbl, month, type_fn, category_fn=lambda label: "工商业用电", fill_down=SHARED_FIELDS):
    """Generic: find header rows (until first row with a 千伏 voltage cell), map columns by header text, then read
    data rows.  type_fn(label_text) -> '单一制'|'两部制'|None from the label cells left of the voltage cell."""
    first = next(i for i, r in enumerate(tbl) if any(c and "千伏" in c and _volt_like(c) for c in r))
    cols = map_header(tbl, first)
    need = {"energy_price", "purchase_price", "tdp", "gov_fund"}
    if not need <= set(cols.values()):
        raise RuntimeError(f"header mapping incomplete: {cols}")
    rows, ttype, label, last = [], None, "", {}
    for r in tbl[first:]:
        vi = next((i for i, c in enumerate(r) if c and _volt_like(c)), None)
        if vi is None:
            continue
        lab = "".join(re.sub(r"\s+", "", c) for c in r[:vi] if c)
        if lab:
            t = type_fn(lab)
            if t:
                ttype, label = t, lab
        if ttype is None:
            raise RuntimeError(f"no tariff type for row {r}")
        volt = re.sub(r"\s+", "", r[vi])
        row = {"month": month, "category": category_fn(label), "tariff_type": ttype, "voltage": volt,
               "voltage_rank": pc.voltage_rank(volt)}
        for c, f in cols.items():
            if c <= vi:
                continue
            raw = r[c] if c < len(r) else None
            v = _cellnum(raw)
            if v is None and raw is None and f in fill_down:
                v = last.get(f)          # merged cell spanning rows
            row[f] = v
            if v is not None:
                last[f] = v
        for f in ["sharp", "peak", "flat", "valley", "deep_valley", "line_loss", "system_op", "demand_price",
                  "capacity_price"]:
            row.setdefault(f, None)
        rows.append(row)
    if not rows:
        raise RuntimeError("no data rows")
    return rows, cols


def _volt_like(c):
    c = re.sub(r"\s+", "", c)
    return bool(re.search(r"(不满1|\d|[)）])千伏", c)) and "千伏安" not in c and len(c) < 16
