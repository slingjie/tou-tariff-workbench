"""国网安徽省电力有限公司 代理购电工商业用户电价表 — official copies (text PDFs only).

ah.sgcc.com.cn has no reachable list and fzggw.ah.gov.cn (省发改委 monthly 通告, HTML table) answers 403 to curl,
so each month is taken from an official text-PDF copy of the same table (identical notice, 执行时间 checked):
  * 95598.cn omg-static (国家电网 网上国网 › 执行的电价和收费标准)                → 2025-11 … 2026-05
    (URLs collected in a real browser from the 95598 list, region 安徽; extensionless /omg-static/<id> URLs are PDFs)
  * 芜湖市发展和改革委员会 通知公告 repost (whfgw.wuhu.gov.cn, PDF attachment;
    file host needs a Referer header, otherwise nginx 403)                       → 2026-06 … 2026-10
(Wuhu posted only JPG posters for 2025-11..2026-04 and a scan for 2026-05; 六安 luan.gov.cn has 1000 px PNGs only.)

2026-08: 国网安徽 also issued weekly (第一~四周) revisions and an 实际结算 table after the fact; this module keeps the
monthly table published before the month (Wuhu repost 2026-07-29), like every other month.

Table layout: 单一制 不满1/1-10/35千伏 (+110/220千伏 from 2026-06, 4th TDP period) and 两部制 1-10/35/110/220千伏及以上;
peak header "1、7-9、12月 高峰" or "2-6、10-11月 高峰时段"; 尖峰 column in 1、7、8、12月 (printed '/' for 单一制);
深谷 column printed in most months for 两部制 rows only ('/' for 单一制) — stored only where printed.
Prices are 4-decimal 元/kWh (代理购电价格 5 decimals).
"""
import calendar, json, os, re, subprocess

from . import sgcc_cms as cms
from .. import parse_common as pc

REGION = {"code": "anhui", "name": "安徽", "grid": "sgcc", "province": "安徽省"}
RAW = os.path.join(pc.DATA, "raw", "anhui")
_months = cms.months_between
TOU_ID_BASE = 51000
WUHU = "https://whfgw.wuhu.gov.cn"

# month -> (file_url, article_url, title, publish_date, via)
SEED = {
    "2025-11": ("https://www.95598.cn/omg-static/99307211508851284171900327147099", None,
                "国网安徽省电力有限公司2025年11月代理购电价格公告", "2025-10-28", "95598.cn omg-static"),
    "2025-12": ("https://www.95598.cn/omg-static/99307211582207808407001265862509", None,
                "国网安徽省电力有限公司2025年12月代理购电价格公告", "2025-11-27", "95598.cn omg-static"),
    "2026-01": ("https://www.95598.cn/omg-static/omg-static/99302061015994879771600570473605.pdf", None,
                "国网安徽省电力有限公司代理购电工商业用户电价表（执行时间：2026年1月）", "2025-12-26", "95598.cn omg-static"),
    "2026-02": ("https://www.95598.cn/omg-static/99307211555610692713200415887831", None,
                "国网安徽省电力有限公司2026年2月代理购电价格公告", "2026-01-28", "95598.cn omg-static"),
    "2026-03": ("https://www.95598.cn/omg-static//omg-static/99302251777962889478301743380399.pdf", None,
                "国网安徽省电力有限公司代理购电工商业用户电价表（执行时间：2026年3月）", None, "95598.cn omg-static"),
    "2026-04": ("https://www.95598.cn/omg-static/99307211538970532430200005956272", None,
                "国网安徽省电力有限公司2026年4月代理购电价格", "2026-03-31", "95598.cn omg-static"),
    "2026-05": ("https://www.95598.cn/omg-static//omg-static/99304272167121337245300199002815.pdf", None,
                "国网安徽省电力有限公司代理购电工商业用户电价表（执行时间：2026年5月）", None, "95598.cn omg-static"),
    "2026-06": (WUHU + "/group4/M00/07/B4/rBwdT2oiOR6AVnhZAAGn_ZZYeKg234.pdf", WUHU + "/fgyw/tzgg/8933261.html",
                "国网安徽省电力有限公司代理购电工商业用户电价表和代理购电价格信息表（6月）", "2026-05-29", "gov_repost:whfgw.wuhu.gov.cn"),
    "2026-07": (WUHU + "/group4/M00/07/D2/rBwdT2pCG9OAM3f5AAGyStWHA5s640.pdf", WUHU + "/fgyw/tzgg/8939634.html",
                "国网安徽省电力有限公司代理购电工商业用户电价表和代理购电价格信息表（7月）", "2026-06-29", "gov_repost:whfgw.wuhu.gov.cn"),
    "2026-08": (WUHU + "/group4/M00/07/FA/rBwdT2ppRmuAHXRxAAHCyIsr-qc268.pdf", WUHU + "/fgyw/tzgg/8946424.html",
                "国网安徽省电力有限公司代理购电工商业用户电价表和代理购电价格信息表（8月）", "2026-07-29", "gov_repost:whfgw.wuhu.gov.cn"),
    "2026-09": (WUHU + "/group4/M00/08/22/rBwdT2qUyyCAVdOFAAGrT7snj5U346.pdf", WUHU + "/fgyw/tzgg/8952921.html",
                "国网安徽省电力有限公司2026年9月代理购电价格公告", "2026-08-31", "gov_repost:whfgw.wuhu.gov.cn"),
    "2026-10": (WUHU + "/group4/M00/08/54/rBwdT2q8VESAC6ASAAGrO7RAFUo890.pdf", WUHU + "/fgyw/tzgg/8959437.html",
                "国网安徽省电力有限公司2026年10月代理购电价格公告", "2026-09-30", "gov_repost:whfgw.wuhu.gov.cn"),
}
NOT_AVAILABLE = {}


def discover(start, end, log=print):
    out = []
    for m in _months(start, end):
        if m in SEED:
            f, art, title, pub, via = SEED[m]
            out.append({"month": m, "title": title, "article_url": art or f, "file_url": f, "file_ext": "pdf",
                        "publish_date": pub, "discovered_via": via})
            log(f"  {m}: {f}")
        elif m in NOT_AVAILABLE:
            log(f"  {m}: MISSING – {NOT_AVAILABLE[m]}")
    return out


def _curl(url, path, referer=None):
    for i in range(3):   # at most 2 retries
        cmd = ["curl", "-sS", "-L", "-m", "60", "-A", pc.UA["User-Agent"], "-o", path, "-w", "%{http_code}", url]
        if referer:
            cmd[1:1] = ["-e", referer]
        p = subprocess.run(cmd, capture_output=True)
        if p.returncode == 0 and p.stdout.strip() == b"200" and open(path, "rb").read(5) == b"%PDF-":
            return
    raise RuntimeError(f"download failed: {url}: rc={p.returncode} {p.stdout[-20:]!r} {p.stderr[-200:]!r}")


def download(ann, log=print):
    os.makedirs(RAW, exist_ok=True)
    p = os.path.join(RAW, f"{ann['month']}.pdf")
    if not (os.path.exists(p) and open(p, "rb").read(5) == b"%PDF-"):
        ref = ann["article_url"] if "wuhu.gov.cn" in ann["file_url"] else None
        _curl(ann["file_url"], p, referer=ref)
        log(f"  downloaded {os.path.relpath(p, pc.ROOT)}")
    ann["file_path"] = p
    ann["sha256"] = pc.sha256_file(p)
    return p


def policy_doc_row(ann):
    y, mm = map(int, ann["month"].split("-"))
    return {"id": pc.doc_id(ann["file_url"]), "region_code": REGION["code"], "kind": "agency_price",
            "title": ann["title"], "doc_no": None, "publish_date": ann.get("publish_date"),
            "effective_from": f"{ann['month']}-01",
            "effective_to": f"{ann['month']}-{calendar.monthrange(y, mm)[1]:02d}",
            "source_url": ann["file_url"], "file_path": os.path.relpath(ann["file_path"], pc.ROOT),
            "sha256": ann.get("sha256"), "fetched_at": None}


EXEC_RE = re.compile(r"执行时间[：:]\s*(\d{4})年(\d{1,2})月")


def parse(ann, template=None, log=print):
    pages = cms.pdf_tables(ann["file_path"])
    if not pages or not pages[0][1].strip():
        raise RuntimeError("PDF has no text layer (scan) – not parsed")
    _, text, tabs = pages[0]
    m = EXEC_RE.search(re.sub(r"\s+", "", text))
    if not m or f"{int(m.group(1)):04d}-{int(m.group(2)):02d}" != ann["month"]:
        raise RuntimeError(f"执行时间 mismatch: {m.group(0) if m else None} vs {ann['month']}")
    if "代理购电工商业用户电价表" not in re.sub(r"\s+", "", text):
        raise RuntimeError("page 1 is not 代理购电工商业用户电价表")
    grids = [g for g in tabs if "电压等级" in cms.grid_text(g)]
    if not grids:
        raise RuntimeError("no tariff table")
    rows = cms.parse_grid(grids[0], ann["month"], category_fn=lambda lab, tt: "工商业用电")
    for r in rows:
        if r["tariff_type"] is None:
            raise RuntimeError(f"row without 单一制/两部制 label: {r['voltage']}")
    ann["parse_method"] = "pdfplumber table (text PDF)"
    return rows


# ---------------------------------------------------------------- TOU 皖发改价格〔2025〕302号
TOU_SOURCE = "https://www.ahsz.gov.cn/public/2655575/195697811.html"   # 宿州市人民政府 repost of the DRC notice
TOU_TITLE = "安徽省发展改革委 安徽省能源局关于进一步完善工商业峰谷分时电价政策有关事项的通知"


def tou_rules():
    doc = cms.tou_doc(REGION["code"], TOU_SOURCE, TOU_TITLE, "皖发改价格〔2025〕302号", "2025-05-30", "2025-07-01")
    summer = {"peak": [(16, 24)], "valley": [(2, 9), (11, 13)]}               # 7-9月
    winter = {"peak": [(15, 23)], "valley": [(12, 14), (23, 6)]}              # 1、12月
    other = {"peak": [(6, 8), (16, 22)], "valley": [(11, 14), (23, 6)]}       # 2-6、10-11月
    hol = lambda base: dict(base, deep_valley=[(11, 15)])
    rules = [
        ([7, 8, 9], "normal", cms.segs_from_hours(summer)),
        ([1, 12], "normal", cms.segs_from_hours(winter)),
        ([2, 3, 4, 5, 6, 10, 11], "normal", cms.segs_from_hours(other)),
        # 尖峰 (>=315kVA 两部制 工业 only): 7月15日-8月31日 20-22; 12月15日-次年1月31日 19-21
        ([7, 8], "sharp", cms.segs_from_hours(dict(summer, sharp=[(20, 22)]))),
        ([12, 1], "sharp", cms.segs_from_hours(dict(winter, sharp=[(19, 21)]))),
        # 节假日深谷 (>=315kVA 两部制 工业 only): 3天及以上节假日 11-15
        ([7, 8, 9], "holiday", cms.segs_from_hours(hol(summer))),
        ([1, 12], "holiday", cms.segs_from_hours(hol(winter))),
        ([2, 3, 4, 5, 6, 10, 11], "holiday", cms.segs_from_hours(hol(other))),
    ]
    return doc, cms.tou_rows(REGION["code"], doc, rules, TOU_ID_BASE)


TOU_NOTES = [
    "Sources: text-PDF copies of the 国网安徽 monthly table – 95598.cn omg-static (2025-11..2026-05) and the 芜湖市发改委 "
    "repost (2026-06..10); same table as the 省发改委 monthly 通告 (fzggw.ah.gov.cn, not reachable by curl).",
    "2026-08: the monthly table published 2026-07-29 is stored; weekly revisions / 实际结算 tables are not.",
    "TOU per 皖发改价格〔2025〕302号 (eff. 2025-07-01, still cited by the 2026-10 notice): 7-9月 峰16-24 谷2-9,11-13; "
    "1、12月 峰15-23 谷12-14,23-6; 其他月 峰6-8,16-22 谷11-14,23-6; rest 平. 峰 +84.3% (1、7-9、12月) / +74%, 谷 −61.8% "
    "on (代理购电价格+输配电价).",
    "day_type 'sharp': 尖峰 (+20% on 峰) only 7月15日-8月31日 20-22 and 12月15日-次年1月31日 19-21; day_type 'holiday': "
    "深谷 (−20% on 谷) 11-15 on holidays of 3+ days. Both only for >=315kVA 两部制 industrial users.",
    "尖峰/深谷 prices are stored only where the monthly table prints them (两部制 rows).",
]


def _expected(r):
    P, t = r.get("purchase_price"), r.get("tdp")
    LL, SO, F = r.get("line_loss"), r.get("system_op"), r.get("gov_fund")
    if None in (P, t, LL, SO, F):
        return {}
    m = int(r["month"][5:])
    k = 0.843 if m in (1, 7, 8, 9, 12) else 0.74
    base, add = P + t, LL + SO + F
    exp = {"flat": r.get("energy_price"), "peak": base * (1 + k) + add, "valley": base * (1 - 0.618) + add,
           "energy_price": P + LL + t + SO + F}
    if r.get("sharp") is not None:
        exp["sharp"] = base * (1 + k) * 1.2 + add
    if r.get("deep_valley") is not None:
        exp["deep_valley"] = base * (1 - 0.618) * 0.8 + add
    return exp


def extra_validate(rows):
    return cms.tou_formula_check(rows, _expected, tol=0.00011)
