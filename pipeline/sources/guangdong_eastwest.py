"""广东东西两翼 — table '东西两翼地区' (汕头 潮州 揭阳 汕尾 阳江 湛江 茂名 肇庆 + 江门 恩平/台山/开平)."""
from . import _guangdong_util as U

REGION = {"code": "guangdong-eastwest", "name": "广东东西两翼", "grid": "csg", "province": "广东省"}
A = U.GDArea(REGION, [("东西两翼地区", None, None), ("东西两翼", None, None)], tou_id_base=31301)
_months = U.months_between
discover, download, parse, policy_doc_row, tou_rules, extra_validate = (
    A.discover, A.download, A.parse, A.policy_doc_row, A.tou_rules, A.extra_validate)
TOU_NOTES = U.TOU_NOTES_GD + ["Area: 汕头、潮州、揭阳、汕尾、阳江、湛江、茂名、肇庆市和江门恩平市、台山市、开平市 (as printed in note 1)."]
