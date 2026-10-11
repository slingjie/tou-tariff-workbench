"""国网陕西省电力有限公司 代理购电工商业用户电价 — 榆林地区.

Same monthly PDF as region ``shaanxi`` (95598.cn omg-static copy, data/raw/shaanxi-yulin/seed.json = same URLs);
the page titled 代理购电工商业用户电价表（榆林地区） is loaded (1.5倍 榆林 page skipped).  The table is published by
国网陕西省电力有限公司 for the 榆林 area (榆林 has its own supply company / TDP for 大工业两部制); 一般工商业 rows equal
the rest of Shaanxi.  policy_doc ids are salted ('#榆林') so the shared file does not collide with shaanxi's rows.
Same TOU policy (陕发改价格〔2025〕1034号 applies to the whole 陕西电网).
"""
from . import shaanxi as sx
from . import sgcc_cms as cms
from . import _northwest_util as nw

REGION = {"code": "shaanxi-yulin", "name": "陕西榆林地区", "grid": "sgcc", "province": "陕西省"}
TOU_ID_BASE = 72000
PAGE_WANT = [r"代理购电工商业用户电价表", r"[（(]榆林地区[）)]"]
ID_SALT = "#榆林"

_months = cms.months_between


def discover(start, end, log=print):
    return nw.seed_discover(REGION["code"], start, end, log)


def download(ann, log=print):
    return nw.download(REGION["code"], ann, log)


def policy_doc_row(ann):
    return nw.policy_doc_row(REGION["code"], ann, ID_SALT)


def parse(ann, template=None, log=print):
    return sx.parse_page(ann, PAGE_WANT, log)


def tou_rules():
    code = REGION["code"]
    doc = nw.tou_doc(code, *sx.TOU_1034, id_salt=ID_SALT)
    rules = [([1, 12], "normal", cms.segs_from_hours(sx._spec((18, 20)))),
             ([7, 8], "normal", cms.segs_from_hours(sx._spec((19, 21)))),
             ([2, 3, 4, 5, 6, 9, 10, 11], "normal", cms.segs_from_hours(sx._spec()))]
    return doc, cms.tou_rows(code, doc, rules, TOU_ID_BASE)


TOU_NOTES = [n.replace("不含榆林地区 (this region) and 榆林地区 (region shaanxi-yulin)",
                       "不含榆林地区 (region shaanxi) and 榆林地区 (this region)") for n in sx.TOU_NOTES]
extra_validate = sx.extra_validate
