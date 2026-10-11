"""广东珠三角 (五市 → 六市) — 广东电网 monthly 代理购电 notice via 南方电网 95598 API (see _guangdong_util).
Table '珠三角五市' (广州 珠海 佛山 中山 东莞) up to 2026-07; from 2026-08 (4th TDP period, 粤发改价格〔2026〕242号) the
notice publishes '珠三角6市' = the five + 江门 (excl. 恩平/台山/开平).  Both are stored under this region code."""
from . import _guangdong_util as U

REGION = {"code": "guangdong-prd5", "name": "广东珠三角(五市→六市)", "grid": "csg", "province": "广东省"}
A = U.GDArea(REGION, [("珠三角五市", None, "2026-07"), ("珠三角6市", "2026-08", None), ("珠三角六市", "2026-08", None)],
             tou_id_base=31001)
_months = U.months_between
discover, download, parse, policy_doc_row, tou_rules, extra_validate = (
    A.discover, A.download, A.parse, A.policy_doc_row, A.tou_rules, A.extra_validate)
TOU_NOTES = U.TOU_NOTES_GD + [
    "Area: to 2026-07 珠三角五市 (广州、珠海、佛山、中山、东莞); from 2026-08 珠三角6市 = the five + 江门 excl. 恩平/台山/开平 "
    "(price-zone change with the 4th TDP period, 粤发改价格〔2026〕242号, eff. 2026-08-01).",
]
