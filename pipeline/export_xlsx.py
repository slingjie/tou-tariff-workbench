"""Export data/tariff.db to exports/分时电价_已入库_2025-11至2026-10.xlsx (official data only).
Usage: python -m pipeline.export_xlsx [--from 2025-11 --to 2026-10]"""
import argparse, csv, json, os, sqlite3
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def months(a, b):
    y, m = map(int, a.split("-")); out = []
    while f"{y:04d}-{m:02d}" <= b:
        out.append(f"{y:04d}-{m:02d}"); m += 1
        if m == 13: y, m = y + 1, 1
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--from", dest="f", default="2025-11"); ap.add_argument("--to", default="2026-10")
    a = ap.parse_args(); ms = months(a.f, a.to)
    con = sqlite3.connect(f"file:{ROOT}/data/tariff.db?mode=ro", uri=True, timeout=120)
    reg = pd.read_sql("select code, name, grid, province from region", con)
    tm = pd.read_sql(f"""select r.name 区域, t.month 月份, t.category 用电分类, t.tariff_type 计价方式, t.voltage 电压等级,
        t.voltage_rank, t.energy_price 电度电价, t.purchase_price 代理购电价格, t.line_loss 上网线损费用, t.tdp 输配电价,
        t.system_op 系统运行费用, t.gov_fund 政府性基金及附加, t.sharp 尖峰, t.peak 高峰, t.flat 平段, t.valley 低谷,
        t.deep_valley 深谷, t.demand_price "需量电价(元/kW·月)", t.capacity_price "容量电价(元/kVA·月)",
        p.title 来源公告, p.source_url 原始公告链接, t.region_code
        from tariff_month t join region r on r.code=t.region_code left join policy_doc p on p.id=t.doc_id
        where t.month between '{ms[0]}' and '{ms[-1]}'
        order by t.region_code, t.month, t.tariff_type, t.category, t.voltage_rank""", con)
    tou = pd.read_sql("""select r.name 区域, u.effective_from 生效日, u.effective_to 失效日, u.months 适用月份, u.day_type 日类型,
        u.segments 时段, p.doc_no 文号, p.title 政策文件, p.source_url 链接
        from tou_rule u join region r on r.code=u.region_code left join policy_doc p on p.id=u.doc_id
        order by u.region_code, u.effective_from, u.id""", con)
    zh = {"sharp": "尖峰", "peak": "高峰", "flat": "平", "valley": "低谷", "deep_valley": "深谷", "deep": "深谷"}
    def fmt(s):
        try:
            return "；".join(f"{a}-{b} {zh.get(k, k)}" for a, b, k in json.loads(s))
        except Exception:
            return s
    tou["时段"] = tou["时段"].map(fmt)
    cov = []
    for _, r in reg.iterrows():
        have = sorted(tm.loc[tm.region_code == r.code, "月份"].unique())
        vf = os.path.join(ROOT, "data", f"validation_{r.code}.csv")
        nfail = max(0, sum(1 for _ in open(vf, encoding="utf-8")) - 1) if os.path.exists(vf) else 0
        cov.append({"区域": r["name"], "电网": r.grid, "覆盖月份": f"{len(have)}/{len(ms)}",
                    "缺失月份": "、".join(m for m in ms if m not in have) or "—",
                    "行数": int((tm.region_code == r.code).sum()),
                    "分时规则条数": int((tou["区域"] == r["name"]).sum()), "校验提示": nfail})
    cov = pd.DataFrame(cov)
    tm = tm.drop(columns=["voltage_rank", "region_code"])
    notes = pd.DataFrame({"说明": [
        "数据全部来自国网/南网/内蒙古电力及发改委等官方免费公告原件，自行解析；未使用 energydc 数值。",
        "单位：电价 元/kWh；需量 元/kW·月；容量 元/kVA·月。",
        "尖峰/深谷仅保存官方价格表明确印出的值；未印出为空，不按规则推算。",
        "缺失月份为官方渠道未取得可读原件，不做估算填补。",
        "校验提示多为公告原值的真实特征（如宁夏峰价低于平价、北京不同电压峰谷系数不同、广西“其他”分摊列未入库、云南系统运行费印为“-”）。",
        "西藏无月度代理购电价格序列，仅收录分时时段规则。"]})
    out = os.path.join(ROOT, "exports", f"分时电价_已入库_{ms[0]}至{ms[-1]}.xlsx")
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        for name, df in [("覆盖情况", cov), ("电价明细", tm), ("分时时段规则", tou), ("说明", notes)]:
            df.to_excel(w, sheet_name=name, index=False)
            ws = w.sheets[name]; ws.freeze_panes = "A2"; ws.auto_filter.ref = ws.dimensions
            for col in ws.columns:
                width = min(60, max(8, max(len(str(c.value or "")) for c in col[:200]) * 1.6))
                ws.column_dimensions[col[0].column_letter].width = width
    print(out, len(tm), "rows", len(tou), "tou rules")


if __name__ == "__main__":
    main()
