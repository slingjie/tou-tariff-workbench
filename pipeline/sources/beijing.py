"""国网北京市电力公司 代理购电工商业用户电价 — seed (95598.cn) + text-PDF tables.

Quirks
* bj.sgcc.com.cn answers HTTP 412 (JS anti-bot) to curl; no stable list page.  The monthly notices
  ("YYYY年M月国网北京市电力公司代理购电价格公示表") are taken from State Grid's 网上国网 portal 95598.cn › 信息公开 ›
  执行的电价和收费标准 (partNo=P2019, region 北京).  The listing is JS-only, so the omg-static file URLs were collected once
  with a browser into data/raw/beijing/seed.json; the files download fine with curl.
* Attachment = 3-page text PDF: p1 代理购电工商业用户电价表 (loaded), p2 执行1.5倍 table (skipped), p3 代理购电价格信息表.
* "Special layout": 单一制 rows 不满1千伏 / 1-10千伏 / 35-110千伏 / 220千伏及以上, 两部制 1-10千伏 / 35-110千伏 / 220千伏及以上
  (7 rows, unchanged by the 4th TDP period).  The TOU float is on the 代理购电价格 only and the 峰平谷 ratio differs by class
  (不满1千伏 单一制 1.71:1:0.36, ≥1千伏 单一制 1.8:1:0.3, 两部制 1.6:1:0.4; 尖峰 = 高峰 ×1.2), so 单一制 1-10千伏 高峰 can exceed
  不满1千伏 高峰 – a real 'voltage_monotonic' validation failure, not a parsing error.
* 需量/容量 prices are integers (51/32 …).  尖峰 printed in 1、7、8、12月; no 深谷.
"""
import json, os, re

from . import sgcc_cms as cms
from .. import parse_common as pc

REGION = {"code": "beijing", "name": "北京", "grid": "sgcc", "province": "北京市"}
RAW = os.path.join(pc.DATA, "raw", REGION["code"])
TOU_ID_BASE = 42000
_months = cms.months_between


def discover(start, end, log=print):
    seed = json.load(open(os.path.join(RAW, "seed.json"), encoding="utf-8"))
    wanted = set(_months(start, end))
    out = []
    for a in seed["articles"]:
        if a["month"] in wanted:
            out.append(dict(a, discovered_via="seed", file_kind="pdf", file_ext="pdf"))
    log(f"  seed: {len(out)} months")
    return sorted(out, key=lambda a: a["month"])


def download(ann, log=print):
    path = os.path.join(RAW, f"{ann['month']}.pdf")
    if not (os.path.exists(path) and os.path.getsize(path) > 1000):
        r = pc.http_get(ann["file_url"], timeout=90)
        if not r.content.startswith(b"%PDF"):
            raise RuntimeError(f"not a PDF: {ann['file_url']}")
        open(path, "wb").write(r.content)
        log(f"  downloaded {os.path.relpath(path, pc.ROOT)}")
    ann["file_path"] = path
    ann["sha256"] = pc.sha256_file(path)
    return path


def policy_doc_row(ann):
    return {"id": pc.doc_id(ann["file_url"]), "region_code": REGION["code"], "kind": "agency_price",
            "title": ann["title"], "doc_no": None, "publish_date": ann.get("publish_date"),
            "effective_from": ann.get("effective_from") or f"{ann['month']}-01", "effective_to": ann.get("effective_to"),
            "source_url": ann["file_url"], "file_path": os.path.relpath(ann["file_path"], pc.ROOT),
            "sha256": ann["sha256"], "fetched_at": pc.now_iso()}


def parse(ann, template=None, log=print):
    pages = cms.pdf_tables(ann["file_path"])
    for i, text, tables in pages:
        t = re.sub(r"\s+", "", text)
        if "代理购电工商业用户电价表" in t and "1.5倍" not in t[:60] and tables:
            ef, et = cms.effective_dates(text, ann["month"])
            if ef[:7] != ann["month"]:
                raise RuntimeError(f"PDF is for {ef[:7]}, expected {ann['month']}")
            ann["effective_from"], ann["effective_to"] = ef, et
            rows = cms.parse_grid(tables[0], ann["month"])
            for r in rows:
                r["category"] = "工商业用电"
            ann["parse_method"] = f"pdfplumber page {i + 1}"
            return rows
    raise RuntimeError("代理购电工商业用户电价表 page not found")


# ---------------------------------------------------------------- arithmetic check (京发改规〔2023〕11号 ratios)
def _ratios(r):
    if r["tariff_type"] == "两部制":
        return 0.6, 0.6
    if r["voltage_rank"] == 1:
        return 0.71, 0.64
    return 0.8, 0.7


def extra_validate(rows):
    def exp(r):
        up, dn = _ratios(r)
        P, f = r["purchase_price"], r["flat"]
        e = {"flat": r["energy_price"], "peak": f + up * P, "valley": f - dn * P}
        if r.get("sharp") is not None and r["voltage_rank"] != 1:
            e["sharp"] = f + up * P + 0.2 * (1 + up) * P
        return e
    return cms.tou_formula_check(rows, exp, tol=2e-6)


# ---------------------------------------------------------------- TOU (京发改规〔2023〕11号)
TOU_11 = ("https://fgw.beijing.gov.cn/fgwzwgk/2024zcwj/bwgfxwj/202308/t20230821_3718725.htm",
          "北京市发展和改革委员会关于进一步完善本市分时电价机制等有关事项的通知", "京发改规〔2023〕11号",
          "2023-08-18", "2023-09-01")
BASE = {"peak": [(10, 13), (17, 22)], "flat": [(7, 10), (13, 17), (22, 23)], "valley": [(23, 7)]}


def tou_rules():
    code = REGION["code"]
    d = cms.tou_doc(code, *TOU_11)
    rules = [([2, 3, 4, 5, 6, 9, 10, 11], "normal", cms.segs_from_hours(BASE)),
             ([7, 8], "normal", cms.segs_from_hours(dict(BASE, sharp=[(11, 13), (16, 17)]))),
             ([1, 12], "normal", cms.segs_from_hours(dict(BASE, sharp=[(18, 21)])))]
    return d, cms.tou_rows(code, d, rules, TOU_ID_BASE)


TOU_NOTES = [
    "TOU periods per 京发改规〔2023〕11号 (2023-08-18, effective 2023-09-01; cited in note 2 of every notice 2025-11..2026-10): "
    "高峰 10-13、17-22, 平 7-10、13-17、22-23, 低谷 23-7; 尖峰 夏季(7、8月) 11-13、16-17, 冬季(1、12月) 18-21. No 深谷, no holiday rule.",
    "尖峰 applies only to users with transformer capacity ≥100千伏安(千瓦) at ≥1千伏 (the notice still prints a 尖峰 price for "
    "不满1千伏 rows in 尖峰 months; stored as printed). All 工商业 users except 地铁/无轨电车/电气化铁路牵引 execute TOU.",
    "Float applies to the 代理购电价格 only (分时电度电价在代理购电价格基础上形成): 峰平谷 ratio 1.71:1:0.36 (不满1千伏 单一制), "
    "1.8:1:0.3 (≥1千伏 单一制), 1.6:1:0.4 (两部制); 尖峰 = 高峰 +20%. Hence 单一制 1-10千伏 高峰/尖峰 can exceed 不满1千伏 – the "
    "voltage_monotonic failures in validation_beijing.csv are real.",
    "Source: 95598.cn (网上国网) 执行的电价和收费标准, region 北京 — text PDFs parsed with pdfplumber (page 1 only; 1.5倍 table skipped). "
    "需量 元/千瓦·月, 容量 元/千伏安·月 (integers on the notice).",
]
