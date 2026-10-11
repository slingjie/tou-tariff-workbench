"""Shared helpers for the 广东 (南方电网) regions: guangdong-prd5 / -jiangmen / -huizhou / -eastwest / -north,
shenzhen, shenzhen-shenshan.

Source (official, free): 南方电网 95598 "南网在线" information API
    POST https://95598.csg.cn/ucs/ma/wt/searchService/queryInformationList
    {"areaCode": "030000" (广东) | "090000" (深圳), "level": "2", "classId": "", "keyword": "代理购电", "pageNo": n, "pageSize": 50}
It returns every monthly notice ("广东电网有限责任公司关于YYYY年M月代理购电工商业用户价格的公告" /
"深圳供电局有限公司YYYY年M月代理购电价格").  The same notice is filed under several columns (classId); we keep one.
广东: `link` is a static HTML page whose body holds a downloadFiles PDF link (+ a JPG preview).
深圳: `link` is the downloadFiles PDF itself.

One 广东 notice = one text PDF with one table per price area (page per area):
  珠三角五市 (to 2026-07) / 珠三角6市 (from 2026-08, = 五市 + 江门 excl. 恩平/台山/开平), 惠州市, 江门市 (to 2026-07),
  东西两翼地区, 粤北山区.  Units 分/kWh (converted to 元/kWh here), 需量/容量 in 元.
One 深圳 notice = 表1-1 深圳市 (own tariff structure), 表1-2 深汕特别合作区 (广东-style table), 表2 代理购电价格表.

Download: the PDF is fetched once into data/raw/_guangdong_notices/<gd|sz>/YYYY-MM.pdf and copied into each
region's data/raw/<code>/YYYY-MM.pdf (curl, max 2 retries).
"""
import json, os, re, shutil, subprocess, time
import pdfplumber
from .. import parse_common as pc

API = "https://95598.csg.cn/ucs/ma/wt/searchService/queryInformationList"
AREA = {"gd": "030000", "sz": "090000"}
TITLE_RE = {"gd": re.compile(r"广东电网有限责任公司关于(\d{4})年(\d{1,2})月代理购电工商业用户价格的公告"),
            "sz": re.compile(r"深圳供电局有限公司(\d{4})年(\d{1,2})月代理购电价格$")}
# preferred columns (classId prefixes); first match wins, else any
PREF_CLASS = {"gd": ["c06263b20bf3", "a2a9be101ac1", "bcdbe6731d0b"],
              "sz": ["8b69d39e1496", "3868e1e1789f", "8a98b1b6a916", "09-help-elec"]}
SHARED = os.path.join(pc.DATA, "raw", "_guangdong_notices")


def months_between(start, end):
    y, m = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def _curl(args, timeout=60):
    last = None
    for i in range(3):  # 1 try + max 2 retries
        p = subprocess.run(["curl", "-sS", "-L", "-m", str(timeout), "-A", pc.UA["User-Agent"]] + args,
                           capture_output=True)
        if p.returncode == 0 and p.stdout:
            return p.stdout
        last = p.stderr.decode(errors="ignore")[:200]
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"curl failed: {args[-1][:120]}: {last}")


def api_list(kind, keyword="代理购电", max_pages=10):
    items = []
    for page in range(1, max_pages + 1):
        body = json.dumps({"areaCode": AREA[kind], "level": "2", "classId": "", "pageNo": page, "pageSize": 50,
                           "version": "cn", "keyword": keyword}, ensure_ascii=False)
        raw = _curl(["-H", "Content-Type: application/json;charset=UTF-8", "-H", "Origin: https://95598.csg.cn",
                     "-H", "Referer: https://95598.csg.cn/", "-X", "POST", API, "--data-binary", body], timeout=40)
        d = json.loads(raw)
        lst = (d.get("data") or {}).get("infoList") or []
        if not lst:
            break
        items += [x for x in lst if "infoTitle" in x]
        if len(lst) < 50:
            break
    return items


def discover_notices(kind, start, end, log=print):
    wanted = set(months_between(start, end))
    by_month = {}
    for it in api_list(kind):
        m = TITLE_RE[kind].search(it["infoTitle"].strip())
        if not m:
            continue
        month = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
        if month not in wanted:
            continue
        by_month.setdefault(month, []).append(it)
    anns = []
    for month in sorted(by_month):
        cands = by_month[month]

        def rank(x):
            for i, p in enumerate(PREF_CLASS[kind]):
                if x["classId"].startswith(p):
                    return i
            return 99
        it = sorted(cands, key=lambda x: (rank(x), x["link"]))[0]
        ann = {"month": month, "title": it["infoTitle"].strip(), "publish_date": it.get("publishTime"),
               "info_id": it.get("infoId"), "class_id": it.get("classId"), "file_ext": "pdf"}
        if "downloadFiles" in it["link"]:
            ann["article_url"] = None
            ann["file_url"] = it["link"]
        else:
            ann["article_url"] = it["link"]
            html = _curl([it["link"]], timeout=40).decode("utf-8", "ignore")
            pdfs = re.findall(r'href="(https://95598\.csg\.cn/ucs/ma/wt/business/downloadFiles\?[^"]+)"', html)
            imgs = re.findall(r'src="(https://95598\.csg\.cn/ucs/sr/info/[^"]+\.(?:jpg|png))"', html)
            if not pdfs:
                log(f"  !! {month}: no PDF link in {it['link']}")
                continue
            ann["file_url"] = pdfs[0].replace("&amp;", "&")
            ann["preview_image"] = imgs[0] if imgs else None
        anns.append(ann)
    return anns


def download_notice(kind, code, ann, log=print):
    raw_dir = os.path.join(pc.DATA, "raw", code)
    os.makedirs(raw_dir, exist_ok=True)
    path = os.path.join(raw_dir, f"{ann['month']}.pdf")
    if not (os.path.exists(path) and os.path.getsize(path) > 1000):
        shared = os.path.join(SHARED, kind, f"{ann['month']}.pdf")
        if not (os.path.exists(shared) and os.path.getsize(shared) > 1000):
            os.makedirs(os.path.dirname(shared), exist_ok=True)
            data = _curl([ann["file_url"]], timeout=90)
            if not data.startswith(b"%PDF"):
                raise RuntimeError(f"not a PDF: {ann['file_url']}")
            open(shared, "wb").write(data)
            log(f"  downloaded {kind} {ann['month']} ({len(data)} B)")
        shutil.copyfile(shared, path)
    ann["file_path"] = os.path.relpath(path, pc.ROOT)
    ann["sha256"] = pc.sha256_file(path)
    return ann


def policy_doc_row(code, ann):
    url = ann["file_url"]
    y, m = map(int, ann["month"].split("-"))
    import calendar
    return {"id": pc.doc_id(url + "#" + code), "region_code": code, "kind": "agency_price", "title": ann["title"],
            "doc_no": None, "publish_date": ann.get("publish_date"),
            "effective_from": f"{y:04d}-{m:02d}-01",
            "effective_to": f"{y:04d}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}",
            "source_url": url, "file_path": ann.get("file_path"), "sha256": ann.get("sha256"),
            "fetched_at": pc.now_iso()}


# ------------------------------------------------------------------ parsing
def _n(s):
    s = pc.norm(s)
    if s in ("", "/", "-", "—"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


HEADER_KEYS = [  # (substring in normalized header, field)
    ("代理购电价格", "purchase_price"), ("线损", "line_loss"), ("输配电价", "tdp"), ("系统运行", "system_op"),
    ("政府性基金", "gov_fund"), ("尖峰", "sharp"), ("高峰", "peak"), ("平时段", "flat"), ("低谷", "valley"),
    ("深谷", "deep_valley"), ("最大需量", "demand_price"), ("变压器容量", "capacity_price"), ("电量电价", "energy_price"),
]
FEN_FIELDS = ["energy_price", "purchase_price", "line_loss", "tdp", "system_op", "gov_fund", "sharp", "peak", "flat",
              "valley", "deep_valley"]


def map_header(tbl, nrows=3):
    cols = {}
    width = max(len(r) for r in tbl)
    for c in range(width):
        txt = "".join(pc.norm(r[c]) for r in tbl[:nrows] if c < len(r) and r[c])
        for key, field in HEADER_KEYS:
            if key in txt and field not in cols.values():
                cols[c] = field
                break
    return cols


AREA_RE = re.compile(r"代理购电(?:工商业)?用户电价表\s*[（(]\s*([^）)]+?)\s*[）)]")
EXEC_RE = re.compile(r"执行时间[:：]\s*(\d{4})\s*年\s*(\d{1,2})\s*月")


def gd_area_tables(path):
    """{area_label: (table_rows, page_text)} for every 代理购电工商业用户电价表 page (1.5倍 tables are not separate)."""
    out = {}
    with pdfplumber.open(path) as pdf:
        for pg in pdf.pages:
            text = pg.extract_text() or ""
            m = AREA_RE.search(text)
            if not m:
                continue
            tbls = [t for t in pg.extract_tables() if t and len(t) > 3]
            if not tbls:
                raise RuntimeError(f"no table on page with {m.group(1)}")
            out[m.group(1)] = (tbls[0], text)
    return out


def rows_from_gd_table(tbl, month, category="工商业用电"):
    cols = map_header(tbl)
    need = {"energy_price", "purchase_price", "tdp", "peak", "flat", "valley"}
    if not need <= set(cols.values()):
        raise RuntimeError(f"header mapping incomplete: {cols}")
    rows, ttype = [], None
    for r in tbl[3:]:
        cells = [c if c is not None else None for c in r]
        if cells[1] and pc.norm(cells[1]) in ("单一制", "两部制"):
            ttype = pc.norm(cells[1])
        volt = pc.norm(cells[2]) if len(cells) > 2 else ""
        if not volt or not ttype or "千伏" not in volt:
            continue
        row = {"month": month, "category": category, "tariff_type": ttype,
               "voltage": (cells[2] or "").replace("\n", "").strip(), "voltage_rank": pc.voltage_rank(volt)}
        for c, f in cols.items():
            v = _n(cells[c]) if c < len(cells) else None
            if v is not None and f in FEN_FIELDS:
                v = round(v / 100.0, 8)
            row[f] = v
        for f in ["sharp", "deep_valley", "line_loss", "demand_price", "capacity_price", "system_op", "gov_fund"]:
            row.setdefault(f, None)
        rows.append(row)
    if not rows:
        raise RuntimeError("no data rows")
    return rows


def exec_month(text):
    m = EXEC_RE.search(text.replace(" ", ""))
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}" if m else None


# ------------------------------------------------------------------ validation shared
def formula_check(rows, tol=1e-8):
    """Exact notice arithmetic (verified on every row): per component c in {代理购电价格, 线损, 输配电价, 系统运行费}
    (分/kWh, rounded half-up to 0.01 分 each): 峰 = 基金 + Σ r(1.7c), 谷 = 基金 + Σ r(0.38c),
    尖峰 = 基金 + Σ r(1.25·r(1.7c)), 平 = 非分时 = Σ components.  (1.7:1:0.38, 尖峰 = 峰 +25%)."""
    from decimal import Decimal as D, ROUND_HALF_UP

    def r2(x):
        return x.quantize(D("0.01"), ROUND_HALF_UP)

    def fen(x):
        return D(str(round(x * 100, 6)))
    fails = []
    for r in rows:
        comps = [r.get(k) for k in ("purchase_price", "line_loss", "tdp", "system_op")]
        if r.get("gov_fund") is None or comps[0] is None or comps[2] is None:
            continue
        c = [fen(x) for x in comps if x is not None]
        F = fen(r["gov_fund"])
        exp = {"flat": sum(c) + F,
               "peak": F + sum(r2(D("1.7") * x) for x in c),
               "valley": F + sum(r2(D("0.38") * x) for x in c),
               "sharp": F + sum(r2(D("1.25") * r2(D("1.7") * x)) for x in c)}
        for k, v in exp.items():
            if r.get(k) is not None and abs(fen(r[k]) - v) > D(str(tol * 100)):
                fails.append({"month": r["month"], "category": r["category"], "tariff_type": r["tariff_type"],
                              "voltage": r["voltage"], "check": f"tou_formula_{k}",
                              "detail": f"{k}={r[k]} expected={float(v) / 100}"})
    return fails


# ------------------------------------------------------------------ TOU
TOU_331 = ("http://www.jiangmen.gov.cn/jmfgj/gkmlpt/content/2/2405/post_2405749.html",
           "关于进一步完善我省峰谷分时电价政策有关问题的通知", "粤发改价格〔2021〕331号", "2021-08-31", "2021-10-01")


def _segs(sharp):
    # peak 10-12, 14-19; valley 0-8; flat else; sharp (尖峰) 11-12, 15-17 inside peak
    if sharp:
        return [["00:00", "08:00", "valley"], ["08:00", "10:00", "flat"], ["10:00", "11:00", "peak"],
                ["11:00", "12:00", "sharp"], ["12:00", "14:00", "flat"], ["14:00", "15:00", "peak"],
                ["15:00", "17:00", "sharp"], ["17:00", "19:00", "peak"], ["19:00", "24:00", "flat"]]
    return [["00:00", "08:00", "valley"], ["08:00", "10:00", "flat"], ["10:00", "12:00", "peak"],
            ["12:00", "14:00", "flat"], ["14:00", "19:00", "peak"], ["19:00", "24:00", "flat"]]


def tou_rules_for(code, doc_tuple, id_base, url_override=None):
    url, title, doc_no, pub, eff = doc_tuple
    doc = {"id": pc.doc_id(url + "#tou#" + code), "region_code": code, "kind": "tou_policy", "title": title,
           "doc_no": doc_no, "publish_date": pub, "effective_from": eff, "effective_to": None,
           "source_url": url_override or url, "file_path": None, "sha256": None, "fetched_at": pc.now_iso()}
    other = [1, 2, 3, 4, 5, 6, 10, 11, 12]
    spec = [([7, 8, 9], "normal", _segs(True)),
            (other, "normal", _segs(False)),
            (other, "high_temp", _segs(True))]
    rules = []
    for i, (months, day_type, segs) in enumerate(spec):
        rules.append({"id": id_base + i, "region_code": code, "effective_from": eff, "effective_to": None,
                      "months": json.dumps(months), "day_type": day_type,
                      "segments": json.dumps(segs), "doc_id": doc["id"]})
    return doc, rules


TOU_NOTES_GD = [
    "TOU per 粤发改价格〔2021〕331号 (eff. 2021-10-01; still cited by every monthly notice 2025-11..2026-10): 全省统一 高峰 10-12、14-19, "
    "低谷 0-8, 其余平段; 峰平谷比价 1.7:1:0.38 on the base (代理购电用户电价不含政府性基金及附加).",
    "尖峰 (sharp): 7、8、9月 whole months, plus any other day when 广州 forecast max temperature ≥35℃ (day_type 'high_temp'), "
    "11-12 and 15-17; sharp = peak +25%. The notice prints a 尖峰 price every month (it applies on high-temperature days), "
    "so sharp is stored for all months as printed.",
    "Prices published in 分/kWh with 6 decimals; stored in 元/kWh (÷100). 1.5倍 rule (note 7) is a text note only, no table.",
    "No 深谷 in 广东.",
]


# ------------------------------------------------------------------ region factory (广东电网 areas)
class GDArea:
    """Bind the generic functions to one price area.  `matchers(month)` -> list of area labels accepted that month."""

    def __init__(self, region, area_labels, tou_id_base, merged_from=None, notes=None):
        self.REGION = region
        self.code = region["code"]
        self.area_labels = area_labels          # list of (label substring, from_month, to_month)
        self.tou_id_base = tou_id_base
        self.notes = notes or []

    def discover(self, start, end, log=print):
        anns = discover_notices("gd", start, end, log=log)
        log(f"  {self.code}: {len(anns)} notices via 95598.csg.cn API")
        return anns

    def download(self, ann, log=print):
        return download_notice("gd", self.code, ann, log=log)

    def policy_doc_row(self, ann):
        return policy_doc_row(self.code, ann)

    def labels_for(self, month):
        return [lab for lab, a, b in self.area_labels if (a is None or month >= a) and (b is None or month <= b)]

    def parse(self, ann, template=None, log=print):
        path = os.path.join(pc.ROOT, ann["file_path"])
        tables = gd_area_tables(path)
        labels = self.labels_for(ann["month"])
        hit = [(k, v) for k, v in tables.items() if any(lab == k for lab in labels)]
        if len(hit) != 1:
            raise RuntimeError(f"{self.code} {ann['month']}: expected 1 table for {labels}, found {list(tables)}")
        label, (tbl, text) = hit[0]
        em = exec_month(text)
        if em and em != ann["month"]:
            raise RuntimeError(f"执行时间 {em} != {ann['month']}")
        rows = rows_from_gd_table(tbl, ann["month"])
        ann["parse_method"] = f"pdfplumber table '{label}'"
        ann.setdefault("area_label", {})
        ann["area_label"] = label
        return rows

    def tou_rules(self):
        return tou_rules_for(self.code, TOU_331, self.tou_id_base)

    def extra_validate(self, rows):
        return formula_check(rows)
