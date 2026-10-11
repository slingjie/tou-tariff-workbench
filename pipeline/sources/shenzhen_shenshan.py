"""深汕特别合作区 — 表1-2 of the monthly 深圳供电局 代理购电 notice (广东-style table, TOU per 粤发改价格〔2021〕331号)."""
import os
import pdfplumber
from . import _guangdong_util as U
from .. import parse_common as pc

REGION = {"code": "shenzhen-shenshan", "name": "深圳深汕特别合作区", "grid": "csg", "province": "广东省"}
CODE = REGION["code"]
_months = U.months_between


def discover(start, end, log=print):
    anns = U.discover_notices("sz", start, end, log=log)
    log(f"  {CODE}: {len(anns)} notices via 95598.csg.cn API")
    return anns


def download(ann, log=print):
    return U.download_notice("sz", CODE, ann, log=log)


def policy_doc_row(ann):
    return U.policy_doc_row(CODE, ann)


def parse(ann, template=None, log=print):
    path = os.path.join(pc.ROOT, ann["file_path"])
    tables = U.gd_area_tables(path)
    hit = [(k, v) for k, v in tables.items() if "深汕" in k]
    if len(hit) != 1:
        raise RuntimeError(f"深汕 table not found: {list(tables)}")
    label, (tbl, text) = hit[0]
    em = U.exec_month(text)
    if em and em != ann["month"]:
        raise RuntimeError(f"执行时间 {em} != {ann['month']}")
    rows = U.rows_from_gd_table(tbl, ann["month"])
    ann["parse_method"] = f"pdfplumber 表1-2 '{label}'"
    return rows


def tou_rules():
    return U.tou_rules_for(CODE, U.TOU_331, 31601)


def extra_validate(rows):
    return U.formula_check(rows)


TOU_NOTES = U.TOU_NOTES_GD + [
    "Area: 深汕特别合作区 (表1-2 of the 深圳供电局 notice). Its note 6 applies 粤发改价格〔2021〕331号 (广东 TOU), not 深圳's 1005号.",
]
