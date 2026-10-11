"""广东江门 (excl. 恩平/台山/开平, which belong to 东西两翼).  Own table '江门市' up to 2026-07; from 2026-08 the notice merges
江门 into '珠三角6市' (粤发改价格〔2026〕242号), so 2026-08+ rows here are the 珠三角6市 table (identical to guangdong-prd5)."""
from . import _guangdong_util as U

REGION = {"code": "guangdong-jiangmen", "name": "广东江门(不含恩平台山开平)", "grid": "csg", "province": "广东省"}
A = U.GDArea(REGION, [("江门市", None, "2026-07"), ("珠三角6市", "2026-08", None), ("珠三角六市", "2026-08", None)],
             tou_id_base=31101)
_months = U.months_between
discover, download, parse, policy_doc_row, tou_rules, extra_validate = (
    A.discover, A.download, A.parse, A.policy_doc_row, A.tou_rules, A.extra_validate)
TOU_NOTES = U.TOU_NOTES_GD + [
    "Area: 江门市 excluding 恩平市、台山市、开平市 (those use 东西两翼). Separate '江门市' table through 2026-07; from 2026-08 the notice "
    "has no 江门 table — 江门 is part of '珠三角6市', so 2026-08+ rows are that table (same values as guangdong-prd5).",
]
