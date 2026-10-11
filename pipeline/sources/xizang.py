"""西藏 — no monthly 代理购电工商业用户电价 series; only the TOU rules of the catalogue (目录) sales tariff are loaded.

Evidence (checked 2026-10-11 with the Hark browser on 95598.cn › 信息公开 › 执行的电价和收费标准, region 西藏):
the whole 西藏 listing holds exactly two documents and no monthly 代理购电 announcement:
  * 藏政办发〔2023〕28号 西藏自治区人民政府办公厅关于进一步优化调整全区上网电价和销售电价引导降低社会用电成本的通知
    (printed 2023-11-14, effective 印发次月1日 = 2023-12-01; 95598 publish 2024-05-23) — text PDF, loaded here;
  * 藏发改价格〔2026〕254号 (2026-09-03) 西藏电网第四监管周期输配电价 notice (scanned) — states that generation- and
    retail-side market trading is organised from 2026-10-01; not a 代理购电 price table, not loaded.
So tariff_month stays empty for xizang; tou_rule = 大工业 分季节峰谷分时 periods of 28号 (丰水期 5-10月 / 枯水期 1-4、
11-12月).  The catalogue prices of 28号 附件3 are recorded in TOU_NOTES only (they are 目录销售电价, not 代理购电 prices).
"""
from . import sgcc_cms as cms
from . import _northwest_util as nw

REGION = {"code": "xizang", "name": "西藏", "grid": "sgcc", "province": "西藏自治区"}
TOU_ID_BASE = 76000

_months = cms.months_between


def discover(start, end, log=print):
    log("  xizang: no monthly 代理购电 announcements exist (95598.cn P2019 西藏 listing checked 2026-10-11)")
    return []


def download(ann, log=print):
    raise RuntimeError("xizang has no announcements")


def parse(ann, template=None, log=print):
    raise RuntimeError("xizang has no announcements")


def policy_doc_row(ann):
    raise RuntimeError("xizang has no announcements")


TOU_28 = ("https://www.95598.cn/omg-static/99308100976112223958902124993974",
          "西藏自治区人民政府办公厅关于进一步优化调整全区上网电价和销售电价引导降低社会用电成本的通知",
          "藏政办发〔2023〕28号", "2023-11-14", "2023-12-01")


def tou_rules():
    code = REGION["code"]
    doc = nw.tou_doc(code, *TOU_28)
    wet = {"flat": [(0, 3), (6, 11), (17, 24)], "valley": [(11, 17)], "deep_valley": [(3, 6)]}
    dry = {"flat": [(0, 9), (13, 19)], "peak": [(9, 13), (22, 24)], "sharp": [(19, 22)]}
    rules = [([5, 6, 7, 8, 9, 10], "normal", cms.segs_from_hours(wet)),
             ([1, 2, 3, 4, 11, 12], "normal", cms.segs_from_hours(dry))]
    return doc, cms.tou_rows(code, doc, rules, TOU_ID_BASE)


TOU_NOTES = [
    "No monthly 代理购电工商业用户电价 is published for 西藏 (95598.cn P2019 西藏 listing: only 藏政办发〔2023〕28号 and the "
    "2026-09-03 TDP notice 藏发改价格〔2026〕254号, which announces market trading from 2026-10-01).  工商业 users pay the 目录销售电价 "
    "of 藏政办发〔2023〕28号 (effective 2023-12-01); tariff_month is therefore empty.",
    "TOU (大工业 分季节峰谷分时, all 大工业 except 电气化铁路牵引): 丰水期 5-10月 平 0:00-3:00、6:00-11:00、17:00-24:00, 低谷 11:00-17:00, "
    "深谷 3:00-6:00; 枯水期 1-4、11-12月 平 0:00-9:00、13:00-19:00, 高峰 9:00-13:00、22:00-24:00, 尖峰 19:00-22:00. "
    "高峰 = 平 ×1.5, 低谷 = 平 ×0.5, 尖峰 = 高峰 ×1.2, 深谷 = 低谷 ×0.8.",
    "Catalogue prices, 28号 附件3 (元/kWh). 大工业 (10千伏 / 35千伏 / 110千伏及以上): 平段 0.67 / 0.66 / 0.65; 丰水期 低谷 0.335 / 0.33 / "
    "0.325, 深谷 0.268 / 0.264 / 0.26; 枯水期 高峰 1.005 / 0.99 / 0.975, 尖峰 1.206 / 1.188 / 1.17 (限制类、淘汰类 +0.06 on 平). "
    "一般工商业 分季节 (220/380伏 / 10 / 35 / 110千伏及以上): 丰水期 0.70 / 0.69 / 0.68 / 0.67, 枯水期 0.76 / 0.75 / 0.74 / 0.73 "
    "(非工业 users excluded from 分季节). 基本电价 10元/千伏安·月 for 专变 大工业 and 一般工商业. Not stored as tariff rows "
    "(目录电价, not 代理购电).",
]
