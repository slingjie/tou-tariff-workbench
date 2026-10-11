"""广东粤北山区 — table '粤北山区' (云浮 河源 梅州 韶关 清远 excl. 连州)."""
from . import _guangdong_util as U

REGION = {"code": "guangdong-north", "name": "广东粤北山区", "grid": "csg", "province": "广东省"}
A = U.GDArea(REGION, [("粤北山区", None, None)], tou_id_base=31401)
_months = U.months_between
discover, download, parse, policy_doc_row, tou_rules, extra_validate = (
    A.discover, A.download, A.parse, A.policy_doc_row, A.tou_rules, A.extra_validate)
TOU_NOTES = U.TOU_NOTES_GD + ["Area: 云浮、河源、梅州、韶关、清远市（不含连州市）."]
