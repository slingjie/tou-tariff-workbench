"""广东惠州 — table '惠州市' of the 广东电网 monthly 代理购电 notice (see _guangdong_util)."""
from . import _guangdong_util as U

REGION = {"code": "guangdong-huizhou", "name": "广东惠州", "grid": "csg", "province": "广东省"}
A = U.GDArea(REGION, [("惠州市", None, None)], tou_id_base=31201)
_months = U.months_between
discover, download, parse, policy_doc_row, tou_rules, extra_validate = (
    A.discover, A.download, A.parse, A.policy_doc_row, A.tou_rules, A.extra_validate)
TOU_NOTES = U.TOU_NOTES_GD + ["Area: 惠州市."]
