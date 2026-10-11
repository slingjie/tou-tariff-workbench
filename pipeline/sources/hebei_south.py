"""国网河北省电力有限公司（河北南网）代理购电工商业用户电价 — thin config over sgcc_cms + ocr_table.

Quirks
* Column col466 ("供电企业电价和收费标准") mixes 代理购电价格公告 with 新能源电价信息表 and policy reposts.
* The attachment is a one-page PDF poster that contains only an embedded image (no text layer, ~4.7 MB,
  3766 px wide, light '国家电网' watermark) with 5 tables: 代理购电工商业用户电价表, 1.5倍 table, 代理购电价格信息表,
  转供电终端用户 table, 转供电 1.5倍 table.  Only the first is loaded.
* OCR cross-validation uses the published rule (notice note 2): 平段 = 代理购电工商业用户电价; 高峰/低谷 = 平段 ± 70% of the
  month's 当月平均购电价格 (one constant B per month, solved from the rows); 尖峰 = 高峰 + 20%; 深谷 = 低谷 − 20%.
* Host he.sgcc.com.cn intermittently answers "Empty reply" (curl rc 52) – the 2-retry policy usually rides it out.
"""
from . import sgcc_cms as cms
from . import ocr_table as ocr
from .. import parse_common as pc

REGION = {"code": "hebei-south", "name": "河北南网", "grid": "sgcc", "province": "河北省"}
CMS = cms.CMSRegion(REGION, "http://www.he.sgcc.com.cn", "466",
                    title_res=[r"国网河北省电力有限公司(\d{4})年(\d{1,2})月代理购电价格"], max_pages=6,
                    exclude_re=r"新能源|转供电主体", file_pref=["pdf", "xls", "xlsx", "jpg", "png"], timeout=60)
_months = cms.months_between
discover = CMS.discover
download = CMS.download
policy_doc_row = CMS.policy_doc_row
TOU_ID_BASE = 4000
OCR_VERSION = 2

COLUMNS = ["energy_price", "purchase_price", "line_loss", "tdp", "gov_fund", "system_op",
           "sharp", "peak", "flat", "valley", "deep_valley", "demand_price", "capacity_price"]
V5 = ["不满1千伏", "1~10千伏", "35千伏", "110千伏", "220千伏及以上"]
LAYOUTS = {10: [("单一制", v) for v in V5] + [("两部制", v) for v in V5],      # 4th TDP period (2026-08 →)
           9: [("单一制", v) for v in V5] + [("两部制", v) for v in V5[1:]]}    # 3rd period: no 两部制 不满1千伏
INFO_ROWS = {"purchase_price": 4, "line_loss": 7, "system_op": 8}   # 序号 in 代理购电价格信息表
COEF = {"peak": 0.70, "valley": 0.70, "sharp_on_peak": 0.20, "deep_on_valley": 0.20}


def parse(ann, template=None, log=print):
    return ocr.cached_parse(ann, REGION["code"], lambda: _parse(ann, log), OCR_VERSION)


def _text_layer(path):
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        return "".join((p.extract_text() or "") for p in pdf.pages).strip()


def _parse(ann, log=print):
    path = ann["file_path"]
    if path.endswith(".pdf") and _text_layer(path):
        raise RuntimeError("text-layer PDF: not handled yet (all 2025-11..2026-10 files seen were image posters)")
    img = ocr.load_image(path)
    tables, a = ocr.find_tables(img, thr=150)
    # poster order is fixed (main, 1.5倍, 信息表, 转供电, 转供电1.5倍); titles OCR poorly through the watermark,
    # so take the first big table whose title shows none of the other tables' markers
    t = None
    for tb in tables:
        if len(tb["hlines"]) < 12:
            continue
        tb["title_ocr"] = ocr.title_above(img, tb)
        if not any(x in tb["title_ocr"] for x in ("1.5", "信息", "转供", "终端", "借", "倍")):
            t = tb
            break
    if t is None:
        raise RuntimeError(f"tariff table not found: {[x.get('title_ocr', '')[:30] for x in tables]}")
    tab = ocr.read_table(img, t, a, scale=1.0, log=log)
    layout = LAYOUTS.get(len(tab["data"]))
    if not layout:
        raise RuntimeError(f"{len(tab['data'])} data rows – no layout")
    info = {}
    try:
        iv = ocr.read_info_table(img, [x for x in tables if x is not t], a, scale=1.0, want=("信息表", "价格信息"), log=log)
        info = {f: iv[k] for f, k in INFO_ROWS.items() if k in iv}
    except RuntimeError:
        pass
    rows, fixes = ocr.solve_rows(tab, COLUMNS, layout, ann["month"], COEF, base_kind="month", tol=2e-6, log=log,
                                 info=info)
    for r in rows:
        r["category"] = "工商业用电"
    bad = [f for f in pc.validate_rows(rows) if f["check"] in ("components_sum", "components_missing", "tou_order")]
    if len({(f["tariff_type"], f["voltage"]) for f in bad}) > len(rows) // 4:
        raise RuntimeError(f"OCR unreliable: {len(bad)} failures, e.g. {bad[0]['detail']}")
    ann["effective_from"], ann["effective_to"] = cms.effective_dates(t.get("title_ocr", ""), ann["month"])
    ann["parse_method"] = "ocr(tesseract eng, embedded poster image)"
    ann["ocr_corrections"] = fixes
    ann["ocr_voltage_digits"] = tab["voltage_digits"]
    return rows


def postprocess(parsed, anns):
    return ocr.regulated_consensus(parsed, anns, fields=("tdp", "demand_price", "capacity_price"))


# ---------------------------------------------------------------- TOU (冀发改能价〔2025〕1115号)
# Official text: scanned 6-page PDF of the provincial notice as reposted by 石家庄市发改委 (石发改价格〔2025〕908号);
# listed on hbdrc.hebei.gov.cn 价格政策法规 (2025-10-14).  Copy kept in web/downloads/hebei_south_tou_1115.pdf.
TOU_1115 = ("https://www.sjz.gov.cn/zfxxgk/attachments/1/202510/21/%E3%80%8A%E5%85%B3%E4%BA%8E%E4%BC%98%E5%8C%96"
            "%E8%B0%83%E6%95%B4%E6%B2%B3%E5%8C%97%E5%8D%97%E7%BD%91%E5%B7%A5%E5%95%86%E4%B8%9A%E5%8F%8A%E5%85%B6"
            "%E4%BB%96%E7%94%A8%E6%88%B7%E5%88%86%E6%97%B6%E7%94%B5%E4%BB%B7%E6%94%BF%E7%AD%96%E7%9A%84%E9%80%9A"
            "%E7%9F%A5%E3%80%8B%EF%BC%88%E5%86%80%E5%8F%91%E6%94%B9%E8%83%BD%E4%BB%B7%E3%80%942025%E3%80%951115"
            "%E5%8F%B7%EF%BC%8920251021155847429.pdf",
            "河北省发展和改革委员会关于优化调整河北南网工商业及其他用户分时电价政策的通知", "冀发改能价〔2025〕1115号",
            "2025-10-14", "2025-11-01")
SEASONS = {
    "spring": ([3, 4, 5], {"deep_valley": [(12, 15)], "valley": [(3, 7), (11, 12)],
                           "flat": [(0, 3), (7, 11), (15, 16)], "peak": [(16, 24)]}),
    "summer": ([6, 7, 8], {"valley": [(1, 7), (12, 14)], "flat": [(0, 1), (7, 12), (14, 16)],
                           "peak": [(16, 19), (22, 24)], "sharp": [(19, 22)]}),
    "autumn": ([9, 10, 11], {"deep_valley": [(12, 14)], "valley": [(2, 6), (11, 12), (14, 15)],
                             "flat": [(0, 2), (6, 11), (15, 16)], "peak": [(16, 24)]}),
    "winter": ([12, 1, 2], {"valley": [(2, 6), (11, 15)], "flat": [(0, 2), (6, 7), (9, 11), (15, 17), (23, 24)],
                            "peak": [(7, 9), (19, 23)], "sharp": [(17, 19)]}),
}


def tou_rules():
    code = REGION["code"]
    d = cms.tou_doc(code, *TOU_1115)
    rules = [(months, "normal", cms.segs_from_hours(spec)) for months, spec in SEASONS.values()]
    return d, cms.tou_rows(code, d, rules, TOU_ID_BASE)


TOU_NOTES = [
    "TOU periods per 冀发改能价〔2025〕1115号 (dated 2025-10-14, effective 2025-11-01, governs the whole 2025-11..2026-10 "
    "window; cited in note 2 of every monthly notice). Four seasons: 春(3-5) 深谷 12-15, 低谷 3-7、11-12, 平 0-3、7-11、15-16, "
    "高峰 16-24; 夏(6-8) 低谷 1-7、12-14, 平 0-1、7-12、14-16, 高峰 16-19、22-24, 尖峰 19-22; 秋(9-11) 深谷 12-14, "
    "低谷 2-6、11-12、14-15, 平 0-2、6-11、15-16, 高峰 16-24; 冬(12-2) 低谷 2-6、11-15, 平 0-2、6-7、9-11、15-17、23-24, "
    "高峰 7-9、19-23, 尖峰 17-19. No holiday/weekend variant.",
    "Float base = 当月平均购电价格 (one constant per month, NOT 代理购电价格+输配电价): 高峰/低谷 = 平段 ± 70%·base, "
    "尖峰 = 平段 + 0.7·base + 20%·(1.7·base), 深谷 = 平段 − 0.7·base − 20%·(0.3·base). 线损、输配电价、系统运行费、基金、历史偏差电费折价 do not float.",
    "The notice prints 尖峰 and 深谷 prices for every row and every month (also 不满1千伏 and months without such a period); "
    "they are stored as printed. Per 1115号 不满1千伏 users do NOT execute 尖峰/深谷, and a price only applies in months whose "
    "season has that period (尖峰: 6-8、12-2月; 深谷: 3-5、9-11月). Notice note 4: in 3-5、9-11月 meters show the 深谷 period "
    "in the '尖峰' register.",
    "Scope: all 代理购电 工商业 users in 河北南网 execute TOU; 行政机关、医院 etc. may opt in. Direct market participants follow "
    "the 2026 河北南部电网中长期交易工作方案 instead.",
    "Layout: 3rd TDP period (to 2026-07, 冀发改能价〔2023〕646号) 单一制 5 rows + 两部制 4 rows (no 两部制 不满1千伏); 4th period "
    "(2026-08 →, 冀发改能价〔2026〕691号) 两部制 gains 不满1千伏 (10 rows) and 需量/容量 prices change. 1.5倍 and 转供电 tables "
    "on the poster are not loaded.",
    "Source: he.sgcc.com.cn col466 attachments – one-page image-only PDF posters; OCR'd with tesseract and cross-validated "
    "(component sum, monthly float base, voltage monotonicity); 2025-11 and 2026-08 visually verified against the poster.",
]
