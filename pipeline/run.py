"""CLI: python -m pipeline.run --region jiangsu --from 2025-11 --to 2026-10

Idempotent: already-downloaded files are skipped; all DB writes are upserts (INSERT OR REPLACE).
"""
import argparse, importlib, json, os, sys, traceback
from . import parse_common as pc
from . import build_json


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", required=True)
    ap.add_argument("--from", dest="start", required=True)
    ap.add_argument("--to", dest="end", required=True)
    ap.add_argument("--skip-discover", action="store_true", help="reuse data/raw/<region>/announcements.json")
    a = ap.parse_args(argv)

    mod = importlib.import_module(f"pipeline.sources.{a.region.replace('-', '_')}")   # hebei-south -> hebei_south
    con = pc.connect()
    pc.upsert(con, "region", mod.REGION)

    raw_dir = os.path.join(pc.DATA, "raw", mod.REGION["code"])
    os.makedirs(raw_dir, exist_ok=True)
    cache = os.path.join(raw_dir, "announcements.json")
    print(f"[1] discover {a.region} {a.start}..{a.end}")
    if a.skip_discover and os.path.exists(cache):
        anns = json.load(open(cache, encoding="utf-8"))
    else:
        anns = mod.discover(a.start, a.end)
    expected = mod._months(a.start, a.end)
    missing = [m for m in expected if m not in {x["month"] for x in anns}]

    print("[2] download")
    for ann in anns:
        try:
            mod.download(ann)
        except Exception as e:
            ann["error"] = f"download: {e}"
            print("  !!", ann["month"], e)

    print("[3] parse")
    parsed, errors = {}, {}
    # text PDFs first, so scanned months can borrow a layout template from a neighbour
    order = sorted([x for x in anns if "file_path" in x], key=lambda x: x["month"])
    pending = []
    for ann in order:
        try:
            parsed[ann["month"]] = mod.parse(ann)
            print(f"  {ann['month']}: {len(parsed[ann['month']])} rows ({ann['parse_method']})")
        except Exception as e:
            pending.append((ann, e))
    for ann, first_err in pending:
        tpl = None
        if hasattr(mod, "make_template"):
            # nearest earlier/later text-parsed month with same TDP period
            cands = sorted(parsed, key=lambda m: abs(int(m.replace('-', '')) - int(ann["month"].replace('-', ''))))
            m_ = int(ann["month"][5:])
            need_sharp = m_ in (12, 1, 7, 8)
            if cands:
                tpl = mod.make_template(parsed[cands[0]], need_sharp)
        try:
            parsed[ann["month"]] = mod.parse(ann, template=tpl)
            print(f"  {ann['month']}: {len(parsed[ann['month']])} rows ({ann['parse_method']})")
        except Exception as e:
            errors[ann["month"]] = f"{first_err} | fallback: {e}"
            print("  !!", ann["month"], errors[ann["month"]])

    if hasattr(mod, "postprocess"):
        # cross-month repairs (e.g. regulated 需量/容量 prices misread by OCR in one month)
        for line in mod.postprocess(parsed, {x["month"]: x for x in anns}) or []:
            print("  postprocess:", line)

    print("[4] load DB")
    for ann in anns:
        if "file_path" not in ann:
            continue
        doc = mod.policy_doc_row(ann)
        pc.upsert(con, "policy_doc", doc)
        for r in parsed.get(ann["month"], []):
            row = {k: r.get(k) for k in pc.TARIFF_FIELDS}
            row.update(region_code=mod.REGION["code"], doc_id=doc["id"])
            pc.upsert(con, "tariff_month", row)
    if hasattr(mod, "tou_rules"):
        tr = mod.tou_rules()
        # either (doc, rules) or [(doc, rules), …] when several policy documents define the periods
        pairs = [tr] if isinstance(tr, tuple) else list(tr)
        for doc, rules in pairs:
            pc.upsert(con, "policy_doc", doc)
            con.execute("DELETE FROM tou_rule WHERE region_code=? AND doc_id=?", (mod.REGION["code"], doc["id"]))
            for r in rules:
                clash = con.execute("SELECT region_code FROM tou_rule WHERE id=? AND region_code<>?",
                                    (r["id"], r["region_code"])).fetchone()
                if clash:  # guard: TOU_ID_BASE collision would silently overwrite another region's rule
                    raise SystemExit(f"tou_rule id {r['id']} already used by {clash[0]}; change TOU_ID_BASE")
                pc.upsert(con, "tou_rule", r)
    con.commit()
    json.dump(anns, open(cache, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print("[5] validate")
    all_rows = [dict(r, month=m) for m, rows in parsed.items() for r in rows]
    fails = pc.validate_rows(all_rows)
    if hasattr(mod, "extra_validate"):
        fails += mod.extra_validate(all_rows)
    vpath = os.path.join(pc.DATA, f"validation_{mod.REGION['code']}.csv")
    pc.write_validation_csv(vpath, fails)
    print(f"  {len(fails)} failures -> {vpath}")

    print("[6] export JSON")
    path, out = build_json.build(mod.REGION["code"], a.start, a.end, notes=getattr(mod, "TOU_NOTES", []))
    print(f"  {path}: {len(out['tariffs'])} tariff rows, {len(out['tou_rules'])} tou rules")

    summary = {"region": a.region, "covered": sorted(parsed), "missing_announcement": missing,
               "parse_errors": errors, "rows": {m: len(v) for m, v in sorted(parsed.items())},
               "validation_failures": len(fails),
               "methods": {x["month"]: x.get("parse_method") for x in anns}}
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return summary


if __name__ == "__main__":
    main()
