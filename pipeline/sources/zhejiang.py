"""国网浙江省电力有限公司 代理工商业用户购电价格公告 — 95598.cn omg-static copies (text PDFs).

zj.sgcc.com.cn has no reachable list, and the Zhejiang government sites that repost the notice (luqiao.gov.cn,
hangzhou.gov.cn, fzggw.zj.gov.cn …) time out from the pipeline network; only their OSS attachment host answers.
The monthly notice "国网浙江省电力有限公司关于YYYY年M月代理工商业用户购电价格的公告" is therefore taken from State
Grid's national site 95598.cn (网上国网 › 信息公开 › 执行的电价和收费标准, region 浙江). The list is JS-only, so the
file URLs (extensionless https://www.95598.cn/omg-static/<id>, served as PDF) were collected once in a real browser
(2026-10-11, notes in web/notes/…/95598_zj_ah-*/) and are listed in SEED; files then download with curl.

Each PDF: pp.1-3 notice text, p.4 表1 代理购电工商业用户电价表 (loaded), 表2 1.5倍 (skipped), 表3 充换电设施 (skipped),
表4 代理购电价格信息表.  Layout changes in the window:
  * to 2026-06: 两部制 大工业用电 ×4 + 两部制 一般工商业用电 ×4 (different 尖峰/高峰 ratios) + 单一制 一般工商业 ×3
    (不满1 / 1~10（20）/ 35千伏及以上); TOU per 浙发改价格〔2024〕21号 (ratios on the whole 平段 price:
    大工业 尖峰1.98 高峰1.65, 一般工商业 尖峰1.65 高峰1.5, 低谷0.45 (1、7、8、12月 0.38), 深谷0.2).
  * 2026-07: one 两部制 class "大工业及一般工商业用电" (adds 不满1千伏) ×5 + 单一制 ×3; TOU per 浙发改价格〔2026〕112号
    (ratios on B = 代理购电价格+线损+系统运行费: 尖峰2.05 高峰1.85 平1 低谷0.4 深谷0.2, + 输配电价 + 基金).
  * from 2026-08 (4th TDP period): 两部制 ×4 (no 不满1千伏) + 单一制 ×5 (110 / 220千伏及以上 added).
系统运行费用 differs for 不满1千伏 rows (merged cells are forward-filled by parse_grid).
深谷 is printed only in holiday months (e.g. 2026-02); '/' otherwise → NULL.
"""
import calendar, os, re, subprocess

from . import sgcc_cms as cms
from .. import parse_common as pc

REGION = {"code": "zhejiang", "name": "浙江", "grid": "sgcc", "province": "浙江省"}
RAW = os.path.join(pc.DATA, "raw", "zhejiang")
_months = cms.months_between
TOU_ID_BASE = 52000
O = "https://www.95598.cn/omg-static/"

# month -> (95598 id, publish date shown on the 95598 list)
SEED = {
    "2025-11": ("99303311837101878803300098240925", "2025-10-28"),
    "2025-12": ("99303311821172056579701397889086", "2025-11-27"),
    "2026-01": ("99303311844830344237900515138044", "2025-12-28"),
    "2026-02": ("99303311881168453259202114751183", "2026-01-28"),
    "2026-03": ("99303311887923461123300925607046", "2026-02-25"),
    "2026-04": ("99303311153955503664201887761638", "2026-03-27"),
    "2026-05": ("99304271903943969599302087925315", "2026-04-27"),
    "2026-06": ("99305281936137093055601745795366", "2026-05-28"),
    "2026-07": ("99306272143723326019300129309725", "2026-06-27"),
    "2026-08": ("99307291128440208978200310150465", "2026-07-28"),
    "2026-09": ("99308292203655086863900236576581", "2026-08-28"),
    "2026-10": ("99309281001531820103000941433648", "2026-09-27"),
}


def discover(start, end, log=print):
    out = []
    for m in _months(start, end):
        if m not in SEED:
            continue
        fid, pub = SEED[m]
        y, mm = m.split("-")
        out.append({"month": m, "title": f"国网浙江省电力有限公司关于{int(y)}年{int(mm)}月代理工商业用户购电价格的公告",
                    "article_url": O + fid, "file_url": O + fid, "file_ext": "pdf", "publish_date": pub,
                    "discovered_via": "95598.cn omg-static (list collected in browser)"})
        log(f"  {m}: {O + fid}")
    return out


def _curl(url, path):
    last = None
    for i in range(3):   # at most 2 retries
        p = subprocess.run(["curl", "-sS", "-L", "-m", "60", "-A", pc.UA["User-Agent"], "-o", path, "-w", "%{http_code}",
                            url], capture_output=True)
        if p.returncode == 0 and p.stdout.strip() == b"200" and open(path, "rb").read(5) == b"%PDF-":
            return
        last = (p.returncode, p.stdout[-10:], p.stderr[-200:])
    raise RuntimeError(f"download failed: {url}: {last}")


def download(ann, log=print):
    os.makedirs(RAW, exist_ok=True)
    p = os.path.join(RAW, f"{ann['month']}.pdf")
    if not (os.path.exists(p) and open(p, "rb").read(5) == b"%PDF-"):
        _curl(ann["file_url"], p)
        log(f"  downloaded {os.path.relpath(p, pc.ROOT)}")
    ann["file_path"] = p
    ann["sha256"] = pc.sha256_file(p)
    return p


def policy_doc_row(ann):
    y, mm = map(int, ann["month"].split("-"))
    return {"id": pc.doc_id(ann["file_url"]), "region_code": REGION["code"], "kind": "agency_price",
            "title": ann["title"], "doc_no": None, "publish_date": ann.get("publish_date"),
            "effective_from": ann.get("effective_from") or f"{ann['month']}-01",
            "effective_to": ann.get("effective_to") or f"{ann['month']}-{calendar.monthrange(y, mm)[1]:02d}",
            "source_url": ann["file_url"], "file_path": os.path.relpath(ann["file_path"], pc.ROOT),
            "sha256": ann.get("sha256"), "fetched_at": None}


def _category(lab, tariff):
    j = "".join(lab)
    if "大工业及一般工商业" in j:
        return "大工业及一般工商业用电"
    if "大工业" in j:
        return "大工业用电"
    if "一般工商业" in j:
        return "一般工商业用电"
    raise RuntimeError(f"unknown class label {lab}")


def parse(ann, template=None, log=print):
    pages = cms.pdf_tables(ann["file_path"])
    hit = None
    for i, text, tabs in pages:
        t = re.sub(r"\s+", "", text)
        if re.search(r"表1[：:]国网浙江省电力有限公司代理购电工商业用户电价表", t):
            hit = (t, tabs)
            break
    if hit is None:
        raise RuntimeError("表1 代理购电工商业用户电价表 not found (scan?)")
    t, tabs = hit
    ann["effective_from"], ann["effective_to"] = cms.effective_dates(t[t.index("表1"):], ann["month"])
    if not ann["effective_from"].startswith(ann["month"]):
        raise RuntimeError(f"表1 执行时间 {ann['effective_from']} ≠ {ann['month']}")
    grids = [g for g in tabs if "电压等级" in cms.grid_text(g)]
    if not grids:
        raise RuntimeError("no table on 表1 page")
    grid = grids[0]   # 表1 is the first table on its page (表2 1.5倍 follows)
    rows = cms.parse_grid(grid, ann["month"], category_fn=_category)
    for r in rows:
        if r["tariff_type"] is None:
            raise RuntimeError(f"row without 单一制/两部制: {r['voltage']}")
    ann["parse_method"] = "pdfplumber table (text PDF, 表1)"
    return rows


# ---------------------------------------------------------------- TOU
TOU21_URL = "https://www.fh.gov.cn/art/2024/2/2/art_1229704397_59137762.html"   # 奉化区政府 repost of the DRC notice
TOU112_URL = "https://fzggw.zj.gov.cn/col/col1229123351/art/2026/art_8282044c7b1b469c82f5fd9d2c27a41d.html"
TOU112_95598 = O + "99306011612219236532400597770498"   # same notice on 95598.cn (text PDF)


def tou_rules():
    d21 = cms.tou_doc(REGION["code"], TOU21_URL, "省发展改革委关于调整工商业峰谷分时电价政策有关事项的通知",
                      "浙发改价格〔2024〕21号", "2024-01-25", "2024-03-01", "2026-06-30")
    spring21 = {"peak": [(8, 11), (13, 17)], "valley": [(0, 8), (11, 13)]}                       # 2-6、9-11月
    summer21 = {"sharp": [(9, 11), (15, 17)], "peak": [(8, 9), (17, 23)], "valley": [(0, 8), (11, 13)]}  # 1、7、8、12月
    hol21 = lambda b: dict(b, deep_valley=[(10, 14)])
    a, z = "2024-03-01", "2026-06-30"
    r21 = [([2, 3, 4, 5, 6, 9, 10, 11], "normal", cms.segs_from_hours(spring21), a, z),
           ([1, 7, 8, 12], "normal", cms.segs_from_hours(summer21), a, z),
           # 深谷: 春节、劳动节、国庆节 10:00-14:00
           ([2, 5, 10], "holiday", cms.segs_from_hours(hol21(spring21)), a, z),
           ([1], "holiday", cms.segs_from_hours(hol21(summer21)), a, z)]
    d112 = cms.tou_doc(REGION["code"], TOU112_URL, "省发展改革委 省能源局关于优化工商业分时电价政策有关事项的通知",
                       "浙发改价格〔2026〕112号", "2026-05-28", "2026-07-01")
    spring = {"peak": [(16, 23)], "valley": [(0, 7), (11, 14)]}
    summer = {"sharp": [(18, 22)], "peak": [(16, 18), (22, 23)], "valley": [(0, 7), (11, 14)]}
    hol = lambda b: dict(b, valley=[(0, 9)], deep_valley=[(9, 15)])
    a = "2026-07-01"
    r112 = [([2, 3, 4, 5, 6, 9, 10, 11], "normal", cms.segs_from_hours(spring), a),
            ([1, 7, 8, 12], "normal", cms.segs_from_hours(summer), a),
            # 劳动节、国庆节假期前三天 + 春节假期: 0-9 低谷, 9-15 深谷
            ([2, 5, 10], "holiday", cms.segs_from_hours(hol(spring)), a),
            ([1], "holiday", cms.segs_from_hours(hol(summer)), a)]
    return [(d21, cms.tou_rows(REGION["code"], d21, r21, TOU_ID_BASE)),
            (d112, cms.tou_rows(REGION["code"], d112, r112, TOU_ID_BASE + 10))]


TOU_NOTES = [
    "Source: 国网浙江 monthly notice PDFs on 95598.cn omg-static (URLs collected from the 95598 list in a browser); "
    "表1 代理购电工商业用户电价表 loaded, 1.5倍 and 充换电设施 tables skipped.",
    "Categories: to 2026-06 两部制 大工业用电 / 两部制 一般工商业用电 / 单一制 一般工商业用电; from 2026-07 两部制 "
    "'大工业及一般工商业用电' (one class) + 单一制 一般工商业用电. 2026-08 starts the 4th TDP period (row set changes).",
    "TOU to 2026-06: 浙发改价格〔2024〕21号 (eff. 2024-03-01): 2-6、9-11月 峰8-11,13-17 平17-24 谷0-8,11-13; 1、7、8、12月 "
    "尖峰9-11,15-17 峰8-9,17-23 平13-15,23-24 谷0-8,11-13; 深谷 10-14 on 春节/劳动节/国庆节 (day_type holiday).",
    "TOU from 2026-07-01: 浙发改价格〔2026〕112号 (2026-05-28; repeals 21号): 2-6、9-11月 峰16-23 平7-11,14-16,23-24 "
    "谷0-7,11-14; 1、7、8、12月 尖峰18-22 峰16-18,22-23 平7-11,14-16,23-24 谷0-7,11-14; 劳动节/国庆节前3天及春节假期 "
    "0-9 谷, 9-15 深谷 (day_type holiday). Ratios 2.05:1.85:1:0.4:0.2 on (代理购电价格+线损+系统运行费). "
    "(浙发改价格〔2026〕142号 is not a TOU document.)",
    "深谷 prices stored only where the table prints them (holiday months); '/' → NULL.",
]


def _expected(r):
    F = r.get("energy_price")
    if F is None:
        return {}
    exp = {"flat": F}
    if r["month"] >= "2026-07":
        P, LL, SO, t, G = (r.get(k) for k in ("purchase_price", "line_loss", "system_op", "tdp", "gov_fund"))
        if None in (P, LL, SO, t, G):
            return exp
        B, add = P + LL + SO, t + G
        exp.update(sharp=2.05 * B + add, peak=1.85 * B + add, valley=0.4 * B + add, deep_valley=0.2 * B + add)
    else:   # 21号: ratios on the whole 平段 price
        big = r["category"] == "大工业用电"
        sw = int(r["month"][5:]) in (1, 7, 8, 12)      # 夏冬季 低谷 0.38, 春秋季 0.45 (observed in every notice)
        exp.update(sharp=F * (1.98 if big else 1.65), peak=F * (1.65 if big else 1.5), valley=F * (0.38 if sw else 0.45),
                   deep_valley=F * 0.2)
    return exp


def extra_validate(rows):
    return cms.tou_formula_check(rows, _expected, tol=0.00001)
