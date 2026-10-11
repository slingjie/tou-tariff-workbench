# Tariff pipeline (代理购电工商业用户电价)

Scrapes the monthly grid-agency purchase price announcements (代理购电工商业用户电价公告) from
**free official sources only** (grid company and provincial 发改委 sites), parses the tariff table,
validates it, loads `data/tariff.db` (schema: `pipeline/schema.sql`) and exports `data/json/<region>.json`.
Never use paid aggregators (energydc etc.).

## Run

```bash
pip install pdfplumber pypdfium2 pytesseract numpy   # + system `tesseract` and `curl`
python -m pipeline.run --region jiangsu --from 2025-11 --to 2026-10
python -m pipeline.run --region jiangsu --from 2025-11 --to 2026-10 --skip-discover   # reuse cached URL list
```

Steps: discover → download (skips files already in `data/raw/<region>/YYYY-MM.pdf`) → parse → upsert into
`data/tariff.db` (`INSERT OR REPLACE`, so re-runs are idempotent) → validate (`data/validation_<region>.csv`)
→ export `data/json/<region>.json`. The discovered announcement list is cached in
`data/raw/<region>/announcements.json` (article URL, file URL, publish date, sha256, parse method, OCR corrections).

HTTP goes through `curl` (see `parse_common.http_get`): in this sandbox Python HTTP clients pick up the
environment proxy, which returns 404 for js.sgcc.com.cn; curl connects directly. Every fetch retries at most twice.

## Output

* `tariff_month` – one row per month × category × tariff_type × voltage. `voltage` keeps the PDF's wording
  (e.g. `1-10（20）千伏`; truncated cells are repaired), `voltage_rank` 1=不满1千伏, 2=1-10(20)千伏, 3=35千伏,
  4=110千伏, 5=220千伏及以上. Jiangsu's single-part tariff has two capacity classes, so they live in
  `category`: `工商业用电` (两部制), `工商业用电(100千伏安及以上)`, `工商业用电(100千伏安以下)` (单一制).
  `sharp` is NULL in months without 尖峰 and for classes that don't get it; `deep_valley` is NULL because 附表2
  doesn't publish it (see JSON `notes`).
* `policy_doc` – kind `agency_price` (one per month, source_url = PDF, sha256, publish date, 执行时间) and
  `tou_policy` (the TOU policy document).
* `tou_rule` – months × day_type (`normal` / `holiday`) → segments `[[start,end,type],…]`.
* `data/json/<region>.json` – `{region, months, tariffs, tou_rules, sources, notes, generated_at}`.

## Validation (`parse_common.validate_rows`)
1. `sharp >= peak > flat > valley (> deep_valley)` where present.
2. `purchase + line_loss + tdp + system_op + gov_fund ≈ energy_price` (±0.0005).
3. Within month/category/tariff_type, a higher voltage never costs more than a lower one (every price field).
Failures go to the CSV; the run never crashes on them.

## Adding a province

Create `pipeline/sources/<region>.py` exposing:

| name | contract |
|---|---|
| `REGION` | dict for the `region` table: `code, name, grid, province` |
| `_months(start, end)` | list of `YYYY-MM` (copy from jiangsu) |
| `discover(start, end)` | list of announcement dicts: `month, title, article_url, file_url, file_ext, publish_date` |
| `download(ann)` | save to `data/raw/<region>/YYYY-MM.<ext>` (skip if present); set `ann["file_path"]`, `ann["sha256"]` |
| `parse(ann, template=None)` | list of row dicts with the `tariff_month` fields (minus region/doc_id); set `ann["parse_method"]` |
| `policy_doc_row(ann)` | dict for `policy_doc` |
| optional `tou_rules()` | `(policy_doc dict, [tou_rule dicts])` – delete+reinsert per doc, so idempotent |
| optional `make_template(rows, need_sharp)` | layout borrowed from a text month for OCR of scanned months |
| optional `TOU_NOTES` | list of strings exported to JSON `notes` |

Use `parse_common` for HTTP, hashing, number parsing (`num`), `voltage_rank`, and DB upserts. Then run
`python -m pipeline.run --region <region> --from … --to …`.

## Jiangsu specifics
* List pages: `http://www.js.sgcc.com.cn/xxgk/<供电公司>/djhsfbz/index.shtml` (+ `index_N.shtml`). Every city
  company reposts the provincial announcement; Nantong (`gwntgdgs`) links the PDF, Huai'an/Suzhou often show
  only JPG scans, so the adapter tries several subfolders and keeps the first article with a PDF.
* 附表2 is on page 4. Layout changed in 2026-08 (4th TDP period, 发改价格〔2026〕1077号 / 苏发改价格发〔2026〕777号):
  单一制 gained 110千伏 and 220千伏及以上 rows (10 → 14 rows/month). 尖峰 column appears in Dec–Jan and Jul–Aug.
* 2025-12's PDF is a scanned image: OCR'd with tesseract (eng, digit whitelist) cell-by-cell after grid detection.
  Row labels come from the neighbouring month (TDP must match row-for-row or OCR is rejected); the regulated
  需量/容量 prices are taken from the template when OCR disagrees (logged in `announcements.json`).
* TOU periods: 苏发改价格发〔2025〕426号 (effective 2025-06-01),
  http://fzggw.jiangsu.gov.cn/art/2025/4/30/art_284_11557001.html

## Region codes with a hyphen
`--region hebei-south` loads module `pipeline/sources/hebei_south.py` (hyphen → underscore); raw files, the
announcement cache and `data/validation_<code>.csv` use the region code (`data/raw/hebei-south/`). OCR regions also
need `pytesseract`, `pypdfium2`, `Pillow` and the system `tesseract` (chi_sim/eng traineddata ship in `pipeline/tessdata`).

## 重庆 (chongqing) specifics — status 2026-10-11: 10/12 months, 83 rows, 2 tou_rules, 0 validation failures
* Source: `http://www.cq.sgcc.com.cn/html/main/col2680/` (alias `cq.sgcc.cn`), titles `代理购电价格公示表-YYYY年M月(发布)`.
  Each article is an image gallery (JPG, no text) → OCR via `ocr_table.py`, cross-validated against the published float
  formula (平段 = 非分时; 高峰/低谷 = 平段 ± 60%/62%·(代理购电价格+输配电价); 尖峰 = 高峰 + 20%·1.6·B), the component sum and
  TDP ordering. OCR results are cached per month in `data/raw/chongqing/ocr_cache/` (key = sha256 + OCR_VERSION).
* `postprocess` = `regulated_consensus` on tdp / 需量 / 容量 within a TDP period (row layout). It fixed 2026-04 单一制
  不满1千伏 tdp 0.2821→0.2321, 2026-07 and 2026-08 两部制 35千伏 需量 36.2/38.2→35.2 (2026-08 checked on the image: 35.2).
* `ALT_PDF`: months whose image is unreadable but whose identical notice exists as a text PDF on State Grid's national
  site 95598.cn are parsed from that PDF instead (source_url = the 95598 PDF). Currently 2026-05
  (`https://www.95598.cn/omg-static//omg-static/99304271882705328964600637163120.pdf`). 2026-01 OCR was verified against
  its 95598 PDF (`data/raw/chongqing/xcheck_2026-01_95598.pdf`): 0 differences.
* Missing: **2025-11** – the article still links `/html/files/2025-10/28/20251028181553653465677.jpg` but it is 404 on
  both hosts, no official copy found; **2026-03** – 1262×892 low-res re-render, OCR unreliable at every threshold/scale
  tried (cached failure in `ocr_cache/2026-03.json`), no 95598 copy found. If a 95598 URL turns up, add it to `ALT_PDF`.
* Layout: to 2026-07 单一制 4 rows (110千伏及以上 merged) + 两部制 4; from 2026-08 (4th TDP period) 单一制 5 + 两部制 4.
  尖峰 printed in 1、7、8、12月 only; no 深谷.
* TOU: 渝发改规范〔2021〕14号 (effective 2022-01-01), still cited by every notice:
  https://fzggw.cq.gov.cn/zwgk/zfxxgkml/zcwj/xzgfxwj/sfzggwxzgfxwj/202112/t20211209_10119678.html
  高峰 11-17、20-22; 平 8-11、17-20、22-24; 谷 0-8; 尖峰 12-14 in 1、7、8、12月.

## 河北南网 (hebei-south) specifics — status 2026-10-11: 12/12 months, 111 rows, 4 tou_rules, 0 validation failures
* Source: `http://www.he.sgcc.com.cn/html/main/col466/`, titles `国网河北省电力有限公司YYYY年M月代理购电价格…`
  (新能源 / 转供电主体 articles excluded). Attachment = one-page PDF with only an embedded 3766-px poster image holding 5
  tables; only 代理购电工商业用户电价表 (the first) is loaded. Host sometimes answers "Empty reply" (curl rc 52).
* OCR cross-validation: 平段 = 非分时; 高峰/低谷 = 平段 ± 70%·(当月平均购电价格, one constant per month); 尖峰 = 高峰 +
  20%·1.7·base; 深谷 = 低谷 − 20%·0.3·base. These relations are used ONLY to validate/repair OCR cells; stored 尖峰/深谷
  are the values printed on the poster (it prints them for every row and month, incl. 不满1千伏 – see JSON notes).
  2025-11 and 2026-08 were checked visually against the poster.
* Layout: to 2026-07 单一制 5 + 两部制 4 (no 两部制 不满1千伏); from 2026-08 (4th TDP period, 冀发改能价〔2026〕691号)
  两部制 adds 不满1千伏 → 10 rows, 需量/容量 change (36.8/35.2/33.6/32/30.4 ; 23/22/21/20/19).
* TOU: 冀发改能价〔2025〕1115号 (2025-10-14, effective 2025-11-01, covers the whole window). Official scan (6 pp., no text)
  reposted by 石家庄市发改委: see `TOU_1115` in `hebei_south.py`; local copy `web/downloads/hebei_south_tou_1115.pdf`.
  4 seasonal rules (春3-5 / 夏6-8 / 秋9-11 / 冬12-2), 深谷 only in spring/autumn, 尖峰 only in summer/winter.

## 辽宁 (liaoning) specifics — status 2026-10-11: 12/12 months, 78 rows, 2 tou_rules, 0 validation failures
* Source: ln.sgcc.com.cn col3021 is unreachable (pipeline network and browser); files are the 95598.cn omg-static copies
  (网上国网 › 执行的电价和收费标准, region 辽宁) listed in `data/raw/liaoning/seed.json` (`live_list=False`).
* PDFs are scans (page 1 = 代理购电工商业用户电价表) → OCR (`ocr_table`), cross-checked by Σ components = 非分时 = 平段,
  峰/谷 = 平 ± 50%·代理购电价格, 尖峰 = 平 + 87.5%·P; 需量/容量 by majority within the TDP period (row count).
  OCR cache `data/raw/liaoning/ocr_cache/` (OCR_VERSION 4). 2026-01, 2026-08 and 2026-09 verified by eye against the
  page images: 0 differences.
* Layout (complete printed table; Liaoning prints no 35千伏 row): to 2026-07 6 rows – 单一制 不满1 / 1~10（20）/ 110（66）,
  两部制 1~10（20）/ 110（66）/ 220及以上; from 2026-08 (发改价格〔2026〕1077号) 8 rows – both types × 不满1 / 1~10（20）/
  110（66）/ 220及以上. 尖峰 printed in 1、7、8、12月 only; no 深谷.
* TOU: 辽发改价格〔2023〕441号 (2023-08-25, eff 2023-09-01), https://fgw.ln.gov.cn/fgw/index/tzgg/2023083014061818174/index.shtml;
  文号 per the DRC's 现行有效行政规范性文件目录（2024年版）. No newer 工商业 TOU document found (2025 docs: 居民电采暖 898号,
  煤改电 only). Since 2025-03 spot-market settlement applies to market users; the printed TOU prices are the reference.

## 天津 (tianjin) specifics — status 2026-10-11: 12/12 months, 120 rows, 3 tou_rules, 0 validation failures
* Source: tj.sgcc.com.cn and fzgg.tj.gov.cn are unreachable; each monthly notice is one JPG poster on 95598.cn omg-static
  (网上国网 › 执行的电价和收费标准, region 天津), URLs in `data/raw/tianjin/seed.json` (`live_list=False`), saved as
  `data/raw/tianjin/YYYY-MM.jpg`. Poster = 代理购电工商业用户电价表 (loaded) + 1.5倍 table (skipped) + 价格信息表 (read only to
  cross-check 代理购电价格/线损/系统运行费).
* OCR (`ocr_table`, cache `data/raw/tianjin/ocr_cache/`, OCR_VERSION 1) cross-validated with the notice's printed arithmetic:
  非分时 = Σ components = 平段; D = round(0.6·(代理购电价格+输配电价), 6); 峰/谷 = 平 ± D; 尖峰 = 峰 + round(0.32·B, 6)
  (checked exact to 8 decimals on every row: `extra_validate` tol 5e-9). Regulated 输配/需量/容量 by majority within the TDP
  period (P3 to 2026-07, P4 from 2026-08 – tdp/需量/容量 change in 2026-08); blank 需量/容量 cells are re-read cell by cell
  (2026-09/10 两部制 1-10千伏 容量 27.0). 2025-12 and 2026-09 verified by eye against the poster: 0 differences.
* Layout: 10 rows every month – 单一制 / 两部制 × 不满1千伏, 1-10千伏, 35千伏-110千伏(不含), 110千伏-220千伏(不含), 220千伏及以上.
  尖峰 printed only for 两部制 (大工业) in 1、2、7、8、12月 (单一制 '-' → NULL); no 深谷.
* TOU: 津发改价管〔2025〕277号 (2025-08-06, eff 2025-10-01, cited by every notice in the window); official PDF on 95598.cn
  (`TOU_277` in `tianjin.py`, copy `web/downloads/tianjin_tou_277.pdf`), also fzgg.tj.gov.cn …/202509/t20250902_7124414.html.
  3 rules: 7-8月 (尖峰 20-22), 1/2/12月 (尖峰 18-20), other months.

## 广东 / 深圳 (guangdong-prd5, guangdong-jiangmen, guangdong-huizhou, guangdong-eastwest, guangdong-north, shenzhen, shenzhen-shenshan) — status 2026-10-11: 12/12 months each, 0 validation failures
* Source: 南方电网 95598 "南网在线" information API (official, free, no login), shared code in `pipeline/sources/_guangdong_util.py`:
  `POST https://95598.csg.cn/ucs/ma/wt/searchService/queryInformationList` with JSON
  `{"areaCode":"030000"|"090000","level":"2","classId":"","keyword":"代理购电","pageNo":1,"pageSize":50,"version":"cn"}`
  (030000 = 广东电网, 090000 = 深圳供电局). Each notice is filed under 3–4 columns (classId); one is kept. 广东 `link` = static
  page `/ucs/sr/info/03/<classId>/<ts>/<infoId>.html` containing a `downloadFiles?documentId=…` PDF link (+ JPG preview);
  深圳 `link` = the downloadFiles PDF. PDFs are cached once in `data/raw/_guangdong_notices/{gd,sz}/YYYY-MM.pdf` and copied to
  each region's raw dir. Text PDFs → pdfplumber tables, header-mapped; units 分/kWh → 元/kWh.
* 广东 notice = one page per price area: 珠三角五市 (to 2026-07) / 珠三角6市 (from 2026-08 = 五市 + 江门 excl. 恩平/台山/开平,
  price-zone change with 粤发改价格〔2026〕242号), 惠州市, 江门市 (to 2026-07 only), 东西两翼地区, 粤北山区. guangdong-jiangmen
  2026-08+ rows are the 珠三角6市 table (no separate 江门 table exists any more). Rows: 6/month to 2026-07, 7 from 2026-08
  (单一制 220千伏及以上 added). 尖峰 is printed every month (applies on high-temperature days) → stored as printed. No 深谷.
* Validation (`extra_validate`): exact notice arithmetic, per component c ∈ {代理购电价格, 线损, 输配电价, 系统运行费}, each rounded
  half-up to 0.01 分: 峰 = 基金 + Σ r(1.7c), 谷 = 基金 + Σ r(0.38c), 尖峰 = 基金 + Σ r(1.25·r(1.7c)), 平 = 非分时 — 0 mismatches.
  guangdong-eastwest cross-checked against the 汕头 gov mirror (shantou.gov.cn …/stgdj/fwxx/, 11 months): 0 differences.
* 深圳 (表1-1, 深圳市 excl. 深汕/前海/招商供电) has its own structure: category = 用电类别 (一 101–3000kVA 两部制, 二 ≥3001kVA 两部制,
  三 ≤100kVA/公变 单一制) + monthly-use tier (250 kWh/kVA·月, 400 kWh/kW·月; higher tier = −2 分 on every price); voltage
  10千伏高供低计 / 10千伏高供高计 / 20 / 110 / 220千伏及以上; class 三 = one row '不分电压等级' (rank NULL) to 2026-07, 5 voltage
  rows from 2026-08 (21 → 25 rows). No 线损 column (included in 输配电价, note 2) → line_loss = 0.0. 需量 and 容量 prices printed
  once per class (merged cell) → copied to each row. shenzhen-shenshan = 表1-2 (广东-style table, 331号 TOU).
* TOU: 广东 粤发改价格〔2021〕331号 (eff. 2021-10-01, still cited in every 2025-11..2026-10 notice; scan copy
  `web/downloads/guangdong_tou_331.pdf` from jiangmen.gov.cn, OCR-checked): 高峰 10-12、14-19, 低谷 0-8, 其余平, 1.7:1:0.38;
  尖峰 11-12、15-17 in 7、8、9月 + on days ≥35℃ in 广州 (stored as day_type `high_temp` for the other months), 尖峰 = 峰 +25%.
  深圳: 深发改〔2021〕1005号 (https://fgw.sz.gov.cn/zwgk/qt/tzgg/content/post_9493596.html, eff. 2022-02-01) – same periods,
  比价 by class/voltage. 3 tou_rules per region (ids 31001+/31101+/31201+/31301+/31401+/31501+/31601+).

## 蒙西 (mengxi, 内蒙古电力集团) specifics — status 2026-10-11: 12/12 months, 93 rows, 2 tou_rules, 0 validation failures
* Source: impc.com.cn unreachable; monthly repost by 正镶白旗人民政府 (zxbq.gov.cn 通知公告, live list via eportal pages, curl OK).
  Article = 3–4 images (unordered; 2026-07 has the main table twice); main table picked by OCR'd title, saved
  `data/raw/mengxi/YYYY-MM.<ext>` (`selection.json`, all images in `img/`).
* OCR (`ocr_table.solve_rows`, base_kind 'month' because only 代理购电价格 P floats): 大风季 峰/谷 = 平 ± 68%/52%·P; 小风季 (6-8月)
  ±54%/56%·P, 尖峰 = 平 + 84.8%·P, 深谷 = 平 − 64.8%·P (printed formula row). P is re-derived from (峰−平)/k when the merged cell is
  misread (2026-08 '10.3234'), then 系统运行费 from Σ. Regulated tdp/需量/容量 by majority per TDP period. Eye-checked 2025-12,
  2026-07, 2026-10: 0 diffs. Layout 单一制3+两部制4 to 2026-07, 5+5 from 2026-08. 尖峰/深谷 printed (all rows) only in 6-8月.
* TOU: 内发改价费字〔2023〕1630号 (eff 2024-02-01), https://fgw.xlgl.gov.cn/fgw/ywgz/zdxm/tzgg/2024010409440178739/index.html

## 南方电网 csg2: 广西 (guangxi) / 贵州 (guizhou) / 海南 (hainan) / 云南 (yunnan) — status 2026-10-11
| region | months | rows | tou_rules | validation failures |
|---|---|---|---|---|
| guangxi | 12/12 | 90 | 0 (unverified, see below) | 21 components_sum (unstored '其他' column) |
| guizhou | 12/12 | 90 | 3 (481号 ×2 ranges + 2026-02 notice) | 0 |
| hainan | 12/12 | 81 | 2 (琼发改规〔2021〕18号) | 0 |
| yunnan | 5/12 (2026-06..10) | 46 | 2 (924号, 51号) | 46 components_missing (系统运行费 printed '-') |
* Source for all four: 南方电网 95598 information API (same as 广东, helpers in `pipeline/sources/_csg2_util.py`):
  `POST https://95598.csg.cn/ucs/ma/wt/searchService/queryInformationList`, keyword 代理购电, areaCode 040000 广西 /
  050000 云南 / 060000 贵州 / 070000 海南. A notice is filed under up to 4 columns (identical attachment); discover tries the
  columns in order until one page carries a downloadFiles PDF (the 贵州 2025-12 page once came back without the link).
  Files → `data/raw/<code>/YYYY-MM.pdf` (云南 2026-07 / 2026-09: the static page holds only one long PNG → `YYYY-MM.png`).
* 广西: titles `YYYY年M月代理购电相关价格表`; p.1 table header-mapped (pdfplumber). To 2026-07 the table has an extra column
  '其他' (= 其他折价, 峰谷分时损益分摊) — no schema field, not stored → Σ components ≠ 电量电价 by that amount in 2025-11/12 and
  2026-02 (the 21 failures). Printed 峰/谷 = 平 ± 50%·(当月)平均上网电价 (信息表 item 5), checked every month. **No tou_rule**: notices
  only say "按自治区发展改革委现行有关规定执行"; the finalised 2024 dynamic-adjustment notice (draft: 峰16-24, 平0-1/7-12/14-16,
  谷1-7/12-14) could not be retrieved from an official source (fgw.gxzf.gov.cn unreachable, not archived; browser notes in
  `web/notes/55dbe641-d867-49ae-80e6-e219c2d53136/gxfgw-tu_6sef4v2t7emt3bz3q2tfot3jwg/`); 桂发改价格〔2023〕609号 is superseded.
* 贵州: titles `贵州电网有限责任公司YYYY年M月代理购电用户电价公告`; p.3 table; 单一制 rows print no TOU prices (NULL). 7 rows to
  2026-07, 9 from 2026-08. TOU float: B = P+输配+线损+系统运行费 to 2026-01, B = P+线损 from 2026-02 (黔发改价格〔2026〕47号),
  峰/谷 = 平 ± 60%·B (checked). TOU: 黔发改价格〔2023〕481号 (official scan on 95598.csg.cn) 峰10-13/17-22 谷0-8, stored for
  2023-08-01..2026-01-31 and from 2026-03-01; **2026-02** notice prints different periods (峰10-11/17-22, 谷0-8/13-15) → stored as
  its own rule (source = that notice). Earlier web_fetch captures (2025-11, 2026-01) were identical in value to the PDFs; their
  `data/raw/guizhou/*.md` + `seed.json` are no longer used.
* 海南: titles `海南电网有限责任公司代理购电工商业用户电价表（YYYY年M月）` (link = PDF). Category carries the class:
  `工商业用电(100千伏安以下)` = 单一制, `工商业用电(100千伏安及以上)` = 两部制. P3: 单一制 2 + 两部制 4 ('35千伏以下' → rank 2);
  P4 (from 2026-08): 5 + 4. 尖峰 printed in 5-7月 only. Float: 峰 +70%, 谷 −60%, 尖峰 = 峰 + 20%·1.7B; B = P+输配 to 2026-07,
  B = P from 2026-08 (checked). TOU 琼发改规〔2021〕18号 (eff 2022-02-01) from the official 95598 page (image + 附件1 xls).
* 云南: titles `云南电网有限责任公司YYYY年M月代理购电工商业用户电价表`; the API holds only 2026-06..2026-10 (2025-11..2026-05 not
  published there; yn.csg.cn unreachable, 楚雄州发改委 repost list 403). Table is an image → `yunnan.py` OCR: one tesseract pass over
  the table (digits, psm 11) + cell re-reads, every row rebuilt from the notice arithmetic (用户电价 = P+线损+输配+基金 = 平;
  峰/谷 = 平 ± 50%·P), readings with a wrong digit count rejected (tesseract drops narrow '1's). All 5 months eye-checked against the
  images: 0 differences. 系统运行费用 printed '-' (settled ex post) → NULL (hence components_missing). TOU 云发改价格〔2024〕924号
  (2025-01-01..2026-02-28) and 〔2026〕51号 (official PDF on 95598.csg.cn, from 2026-03-01), identical periods.

## 浙江 (zhejiang) specifics — status 2026-10-11: 12/12 months, 123 rows, 8 tou_rules, 5 validation failures (genuine)
* Source: 95598.cn omg-static copies of "国网浙江省电力有限公司关于YYYY年M月代理工商业用户购电价格的公告" (text PDFs, 6-7 pp.;
  extensionless `https://www.95598.cn/omg-static/<id>`), ids in `SEED` of `zhejiang.py` (collected in a browser from the
  95598 list, region 浙江). zj.sgcc.com.cn has no list; zj gov sites (luqiao/hangzhou/fzggw.zj) time out from the sandbox.
* Only 表1 代理购电工商业用户电价表 is loaded (1.5倍 / 充换电设施 tables skipped). Categories: to 2026-06 两部制 `大工业用电` ×4,
  两部制 `一般工商业用电` ×4, 单一制 `一般工商业用电` ×3; 2026-07 两部制 `大工业及一般工商业用电` ×5 (adds 不满1千伏) + 单一制 ×3;
  from 2026-08 (4th TDP period) 两部制 ×4 + 单一制 ×5. 深谷 printed only in holiday months (2026-02, 2026-05, 2026-10), else NULL.
* extra_validate: to 2026-06 ratios on the 平段 price (大工业 尖峰1.98/高峰1.65, 一般工商业 1.65/1.5, 低谷 0.45 or 0.38 in
  1、7、8、12月, 深谷 0.2); from 2026-07 (112号) 2.05/1.85/1/0.4/0.2 × (代理购电价格+线损+系统运行费) + 输配 + 基金. All rows pass.
  The 5 failures are 2026-07 两部制 不满1千伏 cheaper than 1~10（20）千伏 (its printed 系统运行费 is lower) – as printed.
* TOU: 浙发改价格〔2024〕21号 (eff 2024-03-01, to 2026-06-30) and 浙发改价格〔2026〕112号 (2026-05-28, eff 2026-07-01, repeals 21号;
  official https://fzggw.zj.gov.cn/col/col1229123351/art/2026/art_8282044c7b1b469c82f5fd9d2c27a41d.html, 95598 copy
  `data/raw/zhejiang/tou_112_95598.pdf`). Holiday 深谷 rules as day_type `holiday`. 浙发改价格〔2026〕142号 is not a TOU doc.

## 安徽 (anhui) specifics — status 2026-10-11: 12/12 months, 90 rows, 8 tou_rules, 0 validation failures
* Source: text-PDF copies of the 国网安徽 monthly table (same table as the 省发改委 monthly 通告 on fzggw.ah.gov.cn, which
  answers 403 to curl): 95598.cn omg-static for 2025-11..2026-05, 芜湖市发改委 repost (whfgw.wuhu.gov.cn, attachment host
  needs a Referer header) for 2026-06..10. URLs in `SEED` of `anhui.py`. Wuhu posted only JPGs before 2026-05.
* 2026-08: the monthly table published 2026-07-29 is stored; the later weekly revisions / 实际结算 tables are not.
* Rows: 单一制 不满1/1-10/35千伏 (+110/220 from 2026-08) and 两部制 1-10/35/110/220千伏及以上. 尖峰 printed (两部制) in 1、7、8、12月,
  深谷 printed for 两部制 in months with a 3+ day holiday (2026-01/02/04/05/06/09/10). extra_validate re-computes 峰/谷/尖峰/深谷 from (代理购电价格+输配电价) × 302号 ratios.
* TOU: 皖发改价格〔2025〕302号 (2025-05-30, eff 2025-07-01, still cited by the 2026-10 notice); official repost
  https://www.ahsz.gov.cn/public/2655575/195697811.html. 3 seasonal groups; day_type `sharp` = 尖峰 windows (7.15-8.31 20-22,
  12.15-1.31 19-21), day_type `holiday` = 3+ day holidays 深谷 11-15 (both only for ≥315kVA 两部制 industrial users).

## 福建 (fujian) TOU fix — 2026-10-11
* tou_rules now cite the official 闽发改规〔2023〕8号 text (https://fgw.fujian.gov.cn/zfxxgkzl/zfxxgkml/bwgfxwj/202311/t20231129_6309872.htm,
  2023-11-28, eff 2024-01-01, valid 5 years → effective_to 2028-12-31); no newer 福建 工商业 TOU document (2026 notices still
  cite 8号). Previously the TOU policy_doc used the 2026-10 notice URL, i.e. the same id as that month's agency_price doc,
  which overwrote it on every run; fixed.

## 西北组 (shaanxi / shaanxi-yulin / gansu / qinghai / xinjiang / xizang) — status 2026-10-11
All provincial sgcc hosts of the group are unreachable; every monthly notice is the **95598.cn omg-static copy**
(网上国网 › 信息公开 › 执行的电价和收费标准, partNo=P2019, region selector), URLs collected with the Hark browser into
`data/raw/<code>/seed.json` (`file_ext` recorded there: omg-static URLs carry no extension). Shared helpers:
`pipeline/sources/_northwest_util.py` (seed discover, download with magic check, salted policy_doc ids, pdfplumber page
picker, merged-cell fill, generic ruled-table OCR reader). Browser notes: `/workspace/web/notes/55dbe641-…/p2019-*/`.

* **陕西 (shaanxi) 12/12, 99 rows; 陕西榆林 (shaanxi-yulin) 12/12, 99 rows; 0 validation failures each.** One text PDF per
  month holds both tables (不含榆林地区 p1 / 榆林地区 p2 or p3; 1.5倍 pages from 2026-05 skipped). pdfplumber puts the merged
  代理购电价格/线损/系统运行费/基金 value in one row of the span (not always the first) → `fill_shared`. 尖峰 column prints 0
  outside 1、7、8、12月 → NULL. Layout 8 rows (一般工商业用电 单一制 ×4 + 大工业用电 两部制 ×4) → 9 from 2026-08 (单一制 220千伏及以上).
  榆林 differs only in 两部制 TDP. yulin policy_doc ids salted '#榆林'. extra_validate: 峰/谷 = 平 ± 70%·P, 尖峰 = 平 + 90%·P (exact).
  TOU 陕发改价格〔2025〕1034号 (2025-07-21, eff 2025-08-01), https://sndrc.shaanxi.gov.cn/zfxxgk/zc/fgwj/sfzggwwj/2025/202507/t20250723_3546317.html
  — 3 rules (1、12月 尖峰 18-20; 7、8月 尖峰 19-21; other months).
* **甘肃 (gansu) 10/12, 76 rows, 0 failures.** 2025-11 and 2025-12 are NOT in the 95598 甘肃 listing (series starts 2026-01;
  searched "代理购电"/"11月"/"12月", all 4 pages) → missing. Formats: PNG posters (01-03, 08-10), image-only PDF (04, 05, 07),
  text PDF (06). Market TOU table: 代理购电价格 and 线损 per 峰/平/谷, no 非分时 → energy_price = 平时段 total, purchase/line_loss
  = 平时段 values, 峰/谷 values in announcements.json `period_prices`. OCR accepted only if every row satisfies 10=1+4+7+8+9,
  11=2+5+7+8+9, 12=3+6+7+8+9 exactly and TDP equals the period list. Eye-checked 2026-01 and 2026-10: 0 diffs.
  Layout 7 rows → 9 from 2026-08. TOU 甘发改价格〔2024〕424号 (signed 2024-07-05, eff 2024-08-01), official scan
  https://www.95598.cn/omg-static/99307021080468606529101523659698 — 1 all-year rule (峰 6-8,18-23; 平 23-6,8-10,16-18; 谷 10-16).
* **青海 (qinghai) 12/12, 90 rows, 0 failures.** Text PDFs (p2 附件1). 7 rows → 9 from 2026-08 (青发改价格〔2026〕405号).
  extra_validate 峰 = 平 + 63%·P, 谷 = 平 − 65%·P (exact). TOU: periods as printed in every notice (峰 7-9,17-23; 谷 9-17; 平 rest)
  = 青海省发改委《关于优化完善我省峰谷分时电价政策的通知》(2024-04-09, eff 2024-05-01); 文号 not captured (fgw.qinghai.gov.cn
  unreachable) → policy_doc source = the 2026-10 notice on 95598. The repost of that policy also has 尖峰 (一、四季度 8-9, 19-21,
  平+100%) but the notices neither mention nor price it → not stored (see JSON notes).
* **新疆 (xinjiang) 12/12, 90 rows, 0 failures.** One poster image per month (769-9450 px wide, resampled to 2270 px). Market
  TOU: merged multi-line 代理购电价格 cell (均价 + 尖峰/高峰/平/低谷/深谷); X = 代理购电价格(X) + 线损 + 输配 + 基金 + 系统运行费;
  非分时 = 平时段. Acceptance: 1=9 per row, TDP list, identical derived period purchase price on every row, and each derived
  period price found in the separately OCR'd merged cell. 2025-11 (967 px) / 2025-12 (769 px): candidate solver reading the
  original pixels with 12 voting variants (fallback; 2025-12 one row's 非分时/平 rebuilt from purchase(平)+components; gov_fund
  0.0041 is also stated in note 1). Eye-checked 2025-11, 2025-12, 2026-02, 2026-10: 0 diffs. 尖峰 printed 11、12、1、7月, 深谷 5-8月.
  TOU 新发改规〔2023〕11号 (2023-08-30, eff 2023-10-01) https://xjdrc.xinjiang.gov.cn/xjfgw/c108361/202312/e5c0f50c7028486ebe36ea253829b340.shtml
  — 4 rules (1/11/12 尖峰 19-21; 7 尖峰 21-23 + 深谷 14-16; 5/6/8 深谷 14-16; other).
* **西藏 (xizang) 0 tariff rows (no 代理购电 series).** 95598 西藏 listing holds only 藏政办发〔2023〕28号 and the TDP notice
  藏发改价格〔2026〕254号 (market trading from 2026-10-01). tou_rule = 28号 大工业 丰水期 (5-10月: 平 0-3,6-11,17-24; 谷 11-17;
  深谷 3-6) / 枯水期 (1-4,11-12月: 平 0-9,13-19; 峰 9-13,22-24; 尖峰 19-22), eff 2023-12-01,
  https://www.95598.cn/omg-static/99308100976112223958902124993974. Catalogue prices recorded in JSON notes only.

## 中部组 central (shandong / henan / hubei / hunan) — status 2026-10-11: 12/12 months each, 0 validation failures, runs idempotent
Shared helper `pipeline/sources/_central_util.py`: seed-based discover (`data/raw/<code>/seed.json`, 95598.cn omg-static URLs
collected with the Hark browser), download with magic-byte sniffing, long-run rule detection (robust to coloured header fills /
watermarks) feeding a cell-grid OCR (ocr_table digit OCR), and `affine_solve` – a coefficient-free cross-check: shared (merged)
components equal on every row, 电度 = Σ components + 输配 = 平段, each TOU price − 电度 affine in the row's 输配电价 over the
month (cells repaired only when ≥ 2 independent cells pin the value; every repair logged in announcements.json). Regulated
输配/需量/容量 by majority within a TDP period (`regulated_postprocess`, also fills blank 需量/容量). Eye-check script
`work/central/eyecheck.py` (values typed from the images): 山东 2025-11, 河南 2025-12 & 2026-09, 湖北 2025-12, 2026-07, 2026-10,
湖南 2026-10 – 0 diffs in the DB.
* **山东 (shandong) 90 rows, 9 tou_rules.** 95598 JPG posters (2025-11..2026-07), image PDFs (08/09), text PDF (10; OCR checked
  against its text layer: 0 diffs). 18 numeric columns (17 before the 新能源可持续发展 column appears); purchase_price = 当月平均购电
  价格 + 容量补偿电价 + 历史偏差电费折价, system_op = 抽蓄 + 煤电容量 + 新能源差价结算 + 各类分摊. 尖峰 and 深谷 printed every month
  for every row (stored). Layout 7 rows → 9 from 2026-08. TOU: 国网山东《关于2026年工商业分时电价公告》(2025-11-28, under
  鲁发改价格〔2023〕914号; 2026 keeps the 2025 periods → effective 2025-01-01..2026-09-30, 5 month groups) and from 2026-10-01
  鲁发改价格函〔2026〕68号 (国网山东 notice 2026-09-11, 4 groups 1-2&12 / 3-6 / 7-8 / 9-11), both with 尖峰 + 深谷 sub-periods.
* **河南 (henan) 99 rows, 5 tou_rules.** 95598 PDFs have no usable text (outlined glyphs, stale CorelDRAW text layer; 02/04/06/07
  are a 1149-px bitmap) → 2025-11..2026-08 parsed as text from the identical 平顶山市人民政府 repost (pds.gov.cn 价格收费 "…公示表
  -水印版.pdf", KaiTi watermark glyphs filtered; seed field `text_pdf_url`); 2026-09/10 OCR of the 95598 PDF rendered at 5000 px
  (2025-11/12 OCR agreed with the text repost cell-for-cell except one 容量 cell, which the period majority fixes). Layout 8 → 9 rows
  from 2026-08. TOU 豫发改价管〔2024〕283号 (eff 2024-06-01), https://fgw.henan.gov.cn/2024/05-29/3000232.html.
* **湖北 (hubei) 90 rows, 3 tou_rules.** Image PDFs: combined 2025年1-12月 and 2026年1-6月 notices (pages in REVERSE month order,
  `page` in seed.json, source_url '#page=N'), monthly from 2026-07. 尖峰 every month. Layout 7 → 9 rows from 2026-08. TOU
  鄂发改价管〔2024〕77号 (eff 2024-05-01) + holiday 深谷 鄂发改价管〔2025〕86号 (春节/劳动节/国庆 9-15, day_type 'holiday').
* **湖南 (hunan) 99 rows, 3 tou_rules.** 95598 PNG posters (no watermark). Energy column = 平时段电度电价 (stored as energy & flat).
  Rows printed high → low voltage; 8 rows → 9 from 2026-08. 尖峰 printed in 1、7、8、12月. TOU 湘发改价调规〔2025〕385号
  (eff 2025-08-01), http://fgw.hunan.gov.cn/fgw/xxgk_70899/zcfg/dfxfg/202507/t20250702_33727950.html.

## 北方组 (shanxi / beijing / heilongjiang / jilin / mengdong) — status 2026-10-11
Shared helper `pipeline/sources/_north_util.py` (`consensus_ocr_only`: regulated 输配/需量/容量 by majority within the TDP
period P3 (<2026-08) / P4 (≥2026-08); only OCR months are ever modified). TOU id bases 41000–45000.

* **山西 (shanxi)** — 12/12 months, 90 rows, 9 tou_rules, 0 validation failures. Live list
  `http://www.sx.sgcc.com.cn/wbw/channels/c40145_N.html` (curl OK) → article → `/homepage/wbw/docs/…pdf` (image-only
  poster 4109×5760, OCR). 2026-03/04 = "代理购电价格更新" re-issues of 2026-04-03. Cross-check: 峰/谷 = 平 ± 60%/55%·当月平均购电价格,
  尖峰 +20%, 深谷 −20%; split-rule filter for watermark strokes; cells snapped to the printed monthly D. Layout 7 rows (P3) /
  9 rows (P4). TOU: 晋发改商品发〔2021〕479号 (to 2026-04-30, periods per notice restatement) → 晋发改商品发〔2026〕15号
  (eff 2026-05-01, https://fgw.shanxi.gov.cn/sxfgwzwgk/sxsfgwxxgk/xxgkml/tz/202602/t20260228_10069158.shtml; 4 seasons, 尖峰 夏/冬,
  holiday 深谷 13-15 春节/劳动节/国庆). 2025-12 and 2026-08 eye-checked: 0 diffs.
* **北京 (beijing)** — 12/12, 84 rows, 3 tou_rules, 13 validation failures (all real `voltage_monotonic`: 单一制 1-10千伏 峰/尖峰 >
  不满1千伏 because ratios differ 1.8 vs 1.71). Text PDFs from 95598.cn P2019 (seed.json). TOU 京发改规〔2023〕11号.
* **黑龙江 (heilongjiang)** — 7/12 (2026-01, 2026-05..10), 63 rows, 1 tou_rule, 0 failures. **Missing 2025-11, 2025-12, 2026-02,
  2026-03, 2026-04**: 95598.cn P2019 listing for 黑龙江 "功能正在维护升级", hl.sgcc.com.cn columns end 2025-03 / 2025-08, no
  official repost found. 2026-05..10 = text PDFs from the 95598 黑龙江 homepage news list; 2026-01 = 网上国网 资讯 article
  `https://www.95598.cn/omg-static/99312281801161322062502015813888.html` (JPG, OCR, eye-checked). TOU 黑发改价格函〔2024〕291号.
* **吉林 (jilin)** — 12/12, 75 rows, 1 tou_rule, 0 failures. 95598 P2019 (7 text PDF + 2026-06/07/09/10 JPG → OCR, 07 & 10
  eye-checked). 峰/谷 = 平 ± 55%·代理购电价格 (7 decimals). TOU 吉发改价格〔2025〕11号 (尖峰 灵活启动 → no 尖峰 rule).
* **蒙东 (mengdong)** — 12/12, 93 rows, 2 tou_rules, 0 failures. 2025-11..2026-06 inline scans on md.sgcc.com.cn
  (信息公开›政策法规及收费标准, img src via browser), 2026-07..10 95598 PDFs (07/08 image). 峰/谷 = 平 + 68% / − 52%·P,
  尖峰/深谷 in 6-8月. TOU 内发改价费字〔2023〕1631号 (eff 2024-02-01). 2025-11 & 2026-07 eye-checked.
All five: two consecutive runs give identical tariff_month + tou_rule hashes (work/north_hash.py).
