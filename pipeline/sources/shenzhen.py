"""深圳 (深圳供电局 供电区域, excl. 深汕特别合作区 / 前海供电 / 招商供电) — 表1-1 of the monthly
"深圳供电局有限公司YYYY年M月代理购电价格" notice, found via the 南方电网 95598 API (areaCode 090000; see _guangdong_util).

表1-1 has Shenzhen's own tariff structure (深发改〔2021〕1005号), mapped as:
* category = 用电类别 (一、工商业用电(101至3000kVA) / 二、工商业用电(3001kVA及以上) / 三、工商业用电(100千伏安及以下和公变接入用电))
  plus, for classes 一/二, the monthly-consumption tier printed in the row ('250kW·h及以下/千伏安·月', '400kW·h以上/千瓦·月' …),
  joined with ' | '.  Classes 一/二 are 两部制 (需量 and 容量 prices printed once per class, merged cell → copied to each row);
  class 三 is 单一制.
* voltage = 原文 (10千伏高供低计(380V/220V计量) / 10千伏高供高计 / 20千伏 / 110千伏 / 220千伏及以上).  Up to 2026-07 class 三
  is a single row without a voltage level → voltage '不分电压等级', voltage_rank NULL.  From 2026-08 class 三 has 5 voltage rows.
* The table has no 上网环节线损 column — note 2: 输配电价「含线损」 — so line_loss is stored as 0.0 (documented in notes).
"""
import os, re
import pdfplumber
from . import _guangdong_util as U
from .. import parse_common as pc

REGION = {"code": "shenzhen", "name": "深圳", "grid": "csg", "province": "广东省"}
CODE = REGION["code"]
_months = U.months_between
TOU_1005 = ("https://fgw.sz.gov.cn/zwgk/qt/tzgg/content/post_9493596.html",
            "深圳市发展和改革委员会关于进一步完善我市峰谷分时电价政策有关问题的通知", "深发改〔2021〕1005号",
            "2021-12-31", "2022-02-01")


def discover(start, end, log=print):
    anns = U.discover_notices("sz", start, end, log=log)
    log(f"  {CODE}: {len(anns)} notices via 95598.csg.cn API")
    return anns


def download(ann, log=print):
    return U.download_notice("sz", CODE, ann, log=log)


def policy_doc_row(ann):
    return U.policy_doc_row(CODE, ann)


def _clean(s):
    return re.sub(r"\s+", "", s or "").replace("／", "/")


def parse(ann, template=None, log=print):
    path = os.path.join(pc.ROOT, ann["file_path"])
    with pdfplumber.open(path) as pdf:
        pg = pdf.pages[0]
        text = pg.extract_text() or ""
        if "表1-1" not in text or "深圳市" not in text:
            raise RuntimeError("表1-1 (深圳市) not on page 1")
        em = U.exec_month(text)
        if em and em != ann["month"]:
            raise RuntimeError(f"执行时间 {em} != {ann['month']}")
        tbl = [t for t in pg.extract_tables() if t and len(t) > 3][0]
    cols = U.map_header(tbl)
    need = {"energy_price", "purchase_price", "tdp", "system_op", "gov_fund", "peak", "flat", "valley"}
    if not need <= set(cols.values()):
        raise RuntimeError(f"header mapping incomplete: {cols}")
    rows, cls, volt, dem, cap = [], None, None, None, None
    for r in tbl[3:]:
        c0, c1, c2 = (_clean(r[0]) if r[0] else None), (r[1] or "").replace("\n", "").strip() or None, _clean(r[2])
        if c0:
            cls, volt = c0, None
            dem = cap = None
        if c1:
            volt = c1
        if not cls:
            continue
        row = {"month": ann["month"]}
        for c, f in cols.items():
            v = U._n(r[c]) if c < len(r) else None
            if v is not None and f in U.FEN_FIELDS:
                v = round(v / 100.0, 8)
            row[f] = v
        if row.get("energy_price") is None:
            continue
        if row.get("demand_price") is not None or row.get("capacity_price") is not None:
            dem, cap = row.get("demand_price"), row.get("capacity_price")
        two_part = "100千伏安及以下" not in cls
        row["demand_price"], row["capacity_price"] = (dem, cap) if two_part else (None, None)
        row["tariff_type"] = "两部制" if two_part else "单一制"
        row["category"] = cls + (" | " + c2 if c2 else "")
        v = volt or "不分电压等级"
        row["voltage"] = v
        row["voltage_rank"] = pc.voltage_rank(v) if volt else None
        row["line_loss"] = 0.0          # included in 输配电价 (表注2); no separate column
        for f in ("sharp", "deep_valley"):
            row.setdefault(f, None)
        rows.append(row)
    if len(rows) < 20:
        raise RuntimeError(f"only {len(rows)} rows parsed")
    ann["parse_method"] = "pdfplumber 表1-1 深圳市"
    return rows


def tou_rules():
    return U.tou_rules_for(CODE, TOU_1005, 31501)


def extra_validate(rows):
    """平段 = 非分时 (exact).  Lower tier (…及以下) / class 三: 尖峰 = 峰 + 25%·(峰 − 基金) (±0.01 分 rounding per component).
    Higher-use tier (…以上): every TOU price = the same class/voltage lower-tier price − 2 分 (1005号: 二档低于一档2分).
    峰/谷 ratios differ by class/voltage (1005号 附件1 比价表), so they are not formula-checked."""
    fails = []

    def f(r, chk, d):
        fails.append({"month": r["month"], "category": r["category"], "tariff_type": r["tariff_type"],
                      "voltage": r["voltage"], "check": chk, "detail": d})
    idx = {(r["month"], r["category"], r["voltage"]): r for r in rows}
    for r in rows:
        if r.get("flat") is not None and abs(r["flat"] - r["energy_price"]) > 1e-8:
            f(r, "flat_eq_energy", f"{r['flat']} vs {r['energy_price']}")
        cat = r["category"]
        if "以上/" in cat:
            lo = idx.get((r["month"], cat.replace("以上/", "及以下/"), r["voltage"]))
            if not lo:
                f(r, "tier_pair_missing", cat)
                continue
            for k in ("energy_price", "tdp", "sharp", "peak", "flat", "valley"):
                if r.get(k) is not None and lo.get(k) is not None and abs(lo[k] - r[k] - 0.02) > 1e-8:
                    f(r, f"tier_minus_2fen_{k}", f"{k}={r[k]} lower-tier={lo[k]}")
        elif r.get("sharp") is not None and r.get("peak") is not None:
            exp = r["peak"] + 0.25 * (r["peak"] - r["gov_fund"])
            if abs(r["sharp"] - exp) > 0.0003:
                f(r, "sharp_25pct", f"sharp={r['sharp']} exp≈{round(exp, 6)}")
    return fails


TOU_NOTES = [
    "TOU per 深发改〔2021〕1005号 (深圳市发展和改革委员会, 2021-12-28, eff. 2022-02-01 per 粤发改价格函〔2021〕2490号), cited by every "
    "monthly notice in the window (note 5): 高峰 10-12、14-19, 低谷 0-8, 其余平段; 尖峰 7、8、9月 whole months plus other days when "
    "广州 max temperature ≥35℃ (day_type 'high_temp'), 11-12 and 15-17, 尖峰 = 峰 +25%. 峰平谷比价 varies by class/voltage "
    "(1005号 附件1 比价表), not a single ratio.",
    "表1-1 (深圳市, excl. 深汕特别合作区、前海供电、招商供电) only; 深汕特别合作区 (表1-2) is region 'shenzhen-shenshan'.",
    "category = 用电类别 + monthly-consumption tier (250 kWh/kVA·月 or 400 kWh/kW·月 thresholds; the higher-use tier is 2 分 cheaper). "
    "Classes 一/二 are 两部制 with both 需量 and 容量 prices printed per class; class 三 (≤100 kVA and 公变接入) is 单一制.",
    "No 上网环节线损 column in 表1-1: line losses are included in the 输配电价 (note 2), stored as line_loss = 0.0.",
    "Up to 2026-07 class 三 is one row without voltage level (voltage '不分电压等级', voltage_rank NULL); from 2026-08 (粤发改价格〔2026〕242号) "
    "it has 5 voltage rows.  Prices in 分/kWh converted to 元/kWh.  Notes 7/8 (娱乐业/商业 flat price; 3001kVA+ may choose class 一) are text only.",
]
