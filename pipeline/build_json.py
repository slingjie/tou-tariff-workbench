"""Export one region from data/tariff.db to data/json/<region>.json for the frontend."""
import json, os
from . import parse_common as pc

TARIFF_COLS = ["month", "category", "tariff_type", "voltage", "voltage_rank", "energy_price", "purchase_price",
               "line_loss", "tdp", "system_op", "gov_fund", "sharp", "peak", "flat", "valley", "deep_valley",
               "demand_price", "capacity_price", "doc_id"]


def build(region_code, start=None, end=None, notes=None):
    con = pc.connect()
    con.row_factory = __import__("sqlite3").Row
    reg = dict(con.execute("SELECT * FROM region WHERE code=?", (region_code,)).fetchone())
    q = f"SELECT {','.join(TARIFF_COLS)} FROM tariff_month WHERE region_code=?"
    args = [region_code]
    if start:
        q += " AND month>=?"; args.append(start)
    if end:
        q += " AND month<=?"; args.append(end)
    q += " ORDER BY month, tariff_type DESC, category, voltage_rank"
    tariffs = [dict(r) for r in con.execute(q, args)]
    months = sorted({t["month"] for t in tariffs})
    tou = []
    for r in con.execute("SELECT * FROM tou_rule WHERE region_code=? ORDER BY id", (region_code,)):
        d = dict(r)
        d["months"] = json.loads(d["months"]); d["segments"] = json.loads(d["segments"])
        tou.append(d)
    sources = [dict(r) for r in con.execute(
        "SELECT id, kind, title, doc_no, publish_date, effective_from, effective_to, source_url, file_path, sha256 "
        "FROM policy_doc WHERE region_code=? ORDER BY kind, effective_from", (region_code,))]
    out = {"region": reg, "months": months, "tariffs": tariffs, "tou_rules": tou, "sources": sources,
           "notes": notes or [], "generated_at": pc.now_iso()}
    os.makedirs(os.path.join(pc.DATA, "json"), exist_ok=True)
    path = os.path.join(pc.DATA, "json", f"{region_code}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    return path, out
