---
title: "代理购电工商业用户电价 + 分时时段规则 数据管线 —— 交接 SOP（分省爬取与维护手册）"
date: "2026-10-11"
lang: zh-CN
---

# 交接说明

- 项目根目录：`/workspace/projects/55dbe641-d867-49ae-80e6-e219c2d53136`（下文所有相对路径均相对于此目录）。
- 本文档面向**没有任何上下文**的接手 agent / 开发者：读完即可独立完成"新月份增量、修复某省、补缺月份、新增区域、对账导出"。
- 事实来源：`pipeline/README.md`、`pipeline/sources.csv`、`pipeline/schema.sql`、`pipeline/*.py`、`pipeline/sources/*.py`（docstring 与常量）、`data/raw/<code>/seed.json` / `announcements.json`、`work/BRIEF_regions.md`、`work/progress_*.md`、`work/compare/our_errors.md`、`web/notes/*.md`，以及 2026-10-11 对 `data/tariff.db` 的实时查询。凡未能核实之处标注 **【待核实】**。
- 数据窗口：2025-11 … 2026-10（12 个月）。数据库快照时间：2026-10-11（北京时间）。
- 本文档只描述现状，**未修改任何管线代码或数据**。文中发现的缺陷中，§1.6 tou_rule 主键冲突与江苏 220 千伏截断已于 2026-10-11 17:10 修复（见对应小节），其余以"待办"形式列出。

---

# 0. 目的与范围

## 0.1 目的

为全国各省（含分区）收集**电网企业代理购电工商业用户电价表**（月度，2025-11 起），以及各省现行**峰谷分时时段规则**，统一入库 `data/tariff.db`，导出前端用的 `data/json/<region>.json` 与 Excel 汇总 `exports/分时电价_已入库_2025-11至2026-10.xlsx`。

## 0.2 范围

- 40 个区域代码（`region` 表）：国网区域（含 `hebei-south` 河北南网、`jibei` 冀北、`mengdong` 蒙东、`shaanxi` / `shaanxi-yulin` 陕西拆分、`xizang` 西藏）、南网区域（广东 5 个价区 + `shenzhen` + `shenzhen-shenshan` + 广西/贵州/海南/云南）、内蒙古电力（`mengxi`）。
  - 精确计数（2026-10-11 查询 `region` 表）：`grid='sgcc'` 28 个，`grid='csg'` 11 个，`grid='impc'` 1 个，合计 **40**。任务描述中"39 个代码"与实际 40 个不一致，以数据库为准（差异来源【待核实】，可能是早期 `pipeline/sources.csv` 中的 `hebei-north` 行后来改名为 `jibei`，或未把 `shenzhen-shenshan` 计入）。
- 每月一份表：只入库"代理购电工商业用户电价表"主表；**1.5 倍表、转供电表、充换电设施表、信息表**不入库（信息表仅用于 OCR 交叉核对）。

## 0.3 硬性约束（用户决策，必须遵守）

| # | 约束 | 说明 |
|---|---|---|
| 1 | **只用官方免费来源** | 省电力公司官网、国网 95598.cn、南网 95598.csg.cn、内蒙古电力、各级发改委/政府网站转载。 |
| 2 | **不登录、不绕付费墙、不买数据** | 遇到登录/付费/验证码 → 记录阻断并换渠道；不得尝试绕过。 |
| 3 | **energydc.cn 只用于"发现线索 + 对账"，绝不入库** | 其 ToS §4："未经授权，任何单位或个人不得擅自使用、复制、修改或传播"。energydc 的任何数值都不得写入 `data/tariff.db` 或 `data/json/`；对账结果仅放 `work/compare/`。修复我方数据时，数值必须取自官方原件（例：河南 2026-10 高峰值取自官方 PDF）。 |
| 4 | **尖峰/深谷只存表内印出的值** | 永不按规则推算；表内为 "-"、"/"、"—"、0（陕西）或空白 → `NULL`。即使表内在"政策上不应执行"的月份/电压也印了值（河北南网、冀北 2026-10），仍按印出值存。 |
| 5 | **缺失月份不估算** | 官方渠道取不到可读原件 → 该月留空，并在模块 `TOU_NOTES` / `seed.json` 的 `missing` 中写明原因。 |
| 6 | **最新官方文件优先** | 分时政策在窗口内变更时，各自写 `effective_from` / `effective_to` 分段存储。 |
| 7 | **江苏 100 千伏安分类写入 `category`** | `工商业用电`（两部制）、`工商业用电(100千伏安及以上)`、`工商业用电(100千伏安以下)`（单一制）。海南沿用同一约定。 |
| 8 | **浙江分时政策** | 2025-11..2026-06 用 浙发改价格〔2024〕21号；**2026-07-01 起** 用 浙发改价格〔2026〕112号（废止 21 号）。**浙发改价格〔2026〕142号不是分时文件**（是新能源就近消纳价格机制，energydc 误标）。 |
| 9 | 单位 | 电价 元/kWh（分/kWh 一律 ÷100）；需量 元/kW·月；容量 元/kVA·月。 |
| 10 | 列映射按表头文字 | 不按列位置；电压等级保留原文（`voltage`），另设 `voltage_rank`。 |
| 11 | OCR 必须交叉校验 | 用公告自身算式（分量求和、峰谷浮动公式）+ 监管价多数投票，且至少目视核对一个月。 |
| 12 | 幂等 | 每个区域连续跑两遍，DB 行与 tou_rule 哈希必须一致。 |
| 13 | 重试上限 | 每个 URL 最多重试 2 次；同一步骤循环超过 2 次即停止，记录原因，转下一个。 |

---

# 1. 系统架构

## 1.1 目录结构

```
pipeline/
  README.md               各组/各省技术说明（英文，最详细的原始记录）
  sources.csv             区域登记表（部分行已过时，见 §1.9）
  schema.sql              4 张表的建表语句
  run.py                  CLI 主流程：discover→download→parse→postprocess→入库→validate→export JSON
  parse_common.py         curl HTTP、sha256、DB 连接/upsert、num()、voltage_rank()、validate_rows()
  build_json.py           导出 data/json/<code>.json
  export_xlsx.py          导出 exports/分时电价_已入库_<from>至<to>.xlsx
  compare_energydc.py     与 energydc 对账（只读 DB）
  tessdata/               chi_sim.traineddata、eng.traineddata（OCR 用）
  sources/
    <region>.py           每个区域一个模块（连字符→下划线，如 hebei_south.py）
    sgcc_cms.py           国网省站通用 CMS 适配器 + parse_grid(按表头映射) + tou_rows/segs_from_hours 等
    ocr_table.py          通用表格 OCR（找表、读格、solve_rows、cached_parse、regulated_consensus）
    _central_util.py      中部组（山东/河南/湖北/湖南）：seed 发现、长线检测、affine_solve、regulated_postprocess
    _north_util.py        北方组：consensus_ocr_only（只改 OCR 月份）
    _northwest_util.py    西北组：seed 发现、魔数校验下载、加盐 doc id、fill_shared、ocr_grid
    _guangdong_util.py    广东/深圳：南网 API、按价区取表、精确公式校验、331号分时
    _csg2_util.py         广西/贵州/海南/云南：南网 API、通用表头映射
    energydc_probe.py / energydc_api_probe.py   仅对账/探测用，不接入 run.py
data/
  tariff.db               SQLite（WAL）
  raw/<code>/             原件 YYYY-MM.<ext>、seed.json、announcements.json、ocr_cache/
  raw/_guangdong_notices/{gd,sz}/YYYY-MM.pdf   广东/深圳公告共享缓存
  raw/energydc/           energydc 原始响应缓存（对账专用，不入库）
  json/<code>.json        前端数据
  validation_<code>.csv   校验失败清单
  tou_2026-10.json        旧版前端演示数据（来源注明"经 energydc.cn 解析"，非官方，待替换，见 §6）
exports/分时电价_已入库_2025-11至2026-10.xlsx
web/downloads/            分时政策原件副本（331号、1115号、291号、吉林11号、1631号、143号、277号、csg2/）
web/notes/                energydc 评估、西南来源笔记
/workspace/web/notes/55dbe641-…/   浏览器任务笔记（95598 P2019 各省列表抓取记录，项目目录之外）
work/                     进度日志 progress_*.md、对账 compare/、各种一次性脚本与日志
docs/plan.md              方案与用户决策记录
```

## 1.2 数据库 schema（`pipeline/schema.sql`）

| 表 | 主键 | 关键字段 | 说明 |
|---|---|---|---|
| `region` | `code` | `name`、`grid`（sgcc / csg / impc）、`province` | 区域登记，由模块 `REGION` dict upsert。 |
| `policy_doc` | `id`（= sha1(source_url)，部分模块加盐） | `region_code`、`kind`（`agency_price` 月度公告 / `tou_policy` 分时政策 / `tdp` 输配电价）、`title`、`doc_no` 文号、`publish_date`、`effective_from`、`effective_to`、`source_url`、`file_path`、`sha256`、`fetched_at` | 每月一条 agency_price；每份分时政策一条 tou_policy。当前：agency_price 451 条，tou_policy 48 条。 |
| `tariff_month` | (`region_code`,`month`,`category`,`tariff_type`,`voltage`) | `voltage_rank`（1=不满1千伏，2=1-10(20)千伏，3=35千伏，4=66/110千伏，5=220/330千伏及以上；深圳"不分电压等级"为 NULL）；`energy_price` 非分时电度电价；`purchase_price` 代理购电价格；`line_loss` 上网环节线损；`tdp` 电度输配电价；`system_op` 系统运行费用折价；`gov_fund` 政府性基金及附加；`sharp`/`peak`/`flat`/`valley`/`deep_valley` 分时电度电价；`demand_price` 需量；`capacity_price` 容量；`doc_id` | 单位：电价 **元/kWh**；需量 **元/kW·月**；容量 **元/kVA·月**。未印出 → NULL。当前 3713 行。 |
| `tou_rule` | `id`（整数，模块自定基数，见 §1.5） | `region_code`、`effective_from`/`effective_to`（YYYY-MM-DD）、`months`（JSON 数组 1..12）、`day_type`（`normal`/`holiday`/`weekend`，实际还用了 `sharp`（安徽）、`high_temp`（广东/深圳））、`segments`（JSON `[["00:00","07:00","valley"],…]`，类型 sharp/peak/flat/valley/deep_valley）、`doc_id` | 当前 136 条。 |

`tariff_month` 字段约定（跨省一致）：
- 市场化分时省份（宁夏、甘肃、新疆）没有"非分时"价：`energy_price` = 平时段总价，`purchase_price`（甘肃还有 `line_loss`）= 平时段值，峰/谷时段的购电价存在 `announcements.json` 的 `period_prices` / `period_purchase_prices`。
- 山东：`purchase_price` = 当月平均购电价格 + 容量补偿电价 + 历史偏差电费折价；`system_op` = 抽蓄容量 + 煤电容量 + 新能源差价结算 + 各类分摊损益（四列之和）。
- 深圳表1-1 无线损列（含在输配电价内）→ `line_loss = 0.0`。
- 云南"系统运行费用"印 "-" → `system_op = NULL`（用户待决，见 §6）。

## 1.3 模块接口（`pipeline/sources/<code>.py`）

| 名称 | 必需 | 约定 |
|---|---|---|
| `REGION` | 是 | `{"code","name","grid","province"}` |
| `_months(start,end)` | 是 | 返回 `YYYY-MM` 列表（多数复用 `sgcc_cms.months_between` / `_central_util.pc_months` / `_guangdong_util.months_between`） |
| `discover(start,end)` | 是 | 返回公告 dict 列表：`month,title,article_url,file_url,file_ext,publish_date`（可附加字段） |
| `download(ann)` | 是 | 存 `data/raw/<code>/YYYY-MM.<ext>`（已存在则跳过），写 `ann["file_path"]`、`ann["sha256"]` |
| `parse(ann, template=None)` | 是 | 返回 `tariff_month` 行（不含 region/doc_id），写 `ann["parse_method"]` |
| `policy_doc_row(ann)` | 是 | 返回 `policy_doc` 行 |
| `tou_rules()` | 否 | 返回 `(doc, [rules])` 或 `[(doc, rules), …]`；run.py 对每个 doc 先 `DELETE FROM tou_rule WHERE region_code=? AND doc_id=?` 再 upsert |
| `make_template(rows, need_sharp)` | 否 | 仅江苏：给扫描月份借用相邻文本月的行模板 |
| `postprocess(parsed, anns)` | 否 | 跨月修复（监管价多数投票、已目视核实的单格修复），返回日志行 |
| `extra_validate(rows)` | 否 | 省内专属算式校验，返回失败 dict 列表 |
| `TOU_NOTES` | 否 | 字符串列表，导出到 JSON `notes`（来源、政策、口径说明都写这里） |

## 1.4 `run.py` 流程与参数

```bash
python -m pipeline.run --region <code> --from 2025-11 --to 2026-10 [--skip-discover]
```

- `--region`：区域代码；连字符自动转下划线找模块（`hebei-south` → `pipeline/sources/hebei_south.py`），原件目录、缓存、校验 CSV 仍用带连字符的代码。
- `--from/--to`：月份区间（必填）。
- `--skip-discover`：直接读 `data/raw/<code>/announcements.json`（含 `file_path`），不访问列表页 —— **离线复跑、幂等验证都用它**。
- 步骤：`[1] discover` → `[2] download`（已存在跳过）→ `[3] parse`（先文本月，失败的再带模板重试）→ `postprocess` → `[4] load DB`（`INSERT OR REPLACE` policy_doc / tariff_month；tou_rules 先删后插）→ 写回 `announcements.json` → `[5] validate`（`validate_rows` + `extra_validate` → `data/validation_<code>.csv`）→ `[6] export JSON`（`build_json.build`）→ 打印 summary（covered / missing_announcement / parse_errors / rows / methods）。
- 注意：`INSERT OR REPLACE` 只按主键覆盖，**不会删除旧行**。若改了 `voltage`/`category` 文字（主键变化），旧行会残留 —— 需先手工 `DELETE FROM tariff_month WHERE region_code=? AND month=?` 再跑（csg2 组 2026-10-11 就做过一次"删除陈旧行后重载"）。

## 1.5 tou_rule ID 基数（各模块 `TOU_ID_BASE`）

| 区域 | 基数 / 当前 id 范围 | 区域 | 基数 / 当前 id 范围 |
|---|---|---|---|
| jiangsu | 1000（1001–1007，jiangsu.py 内硬编码 1000+i） | shanxi | 41000（41001–41057，第二份文件 +50） |
| shanghai | 2000（2001–2055，28号 +50） | beijing | 42000 |
| chongqing | 3000 | heilongjiang | 43000 |
| hebei-south | 4000 | jilin | 44000 |
| fujian | 5000 | mengdong | 45000 |
| ningxia | 7000（第二份 +50） | yunnan | 62000（第 i 份 +10·i） |
| jiangxi | 8000 | guizhou | 63000（2026-02 例外规则 +10） |
| sichuan | 9000 | hainan | 64000 |
| jibei | 10000（1275号 +50） | mengxi | 65000 |
| liaoning | 11000 | shaanxi | 71000 |
| tianjin | 12000 | shaanxi-yulin | 72000 |
| shandong | **21000**（68号 +10） | gansu | 73000 |
| **anhui** | **51000**（2026-10-11 由 21000 改） | qinghai | 74000 |
| henan | **22000** | xinjiang | 75000 |
| **zhejiang** | **52000**（2026-10-11 由 22000 改；112号 +10） | xizang | 76000 |
| hubei | 23000（86号 +10） | guangdong-prd5/jiangmen/huizhou/eastwest/north | 31001 / 31101 / 31201 / 31301 / 31401 |
| hunan | 24000 | shenzhen / shenzhen-shenshan | 31501 / 31601 |
| guangxi | 无（不存 tou_rule） | | |

分组习惯：西南/早期 1000–12000；中部组 21000–24000；浙皖 51000–52000；广东组 31001–31601；北方组 41000–45000；csg2 组 62000–65000；西北组 71000–76000。**新增区域必须选一个未被占用的基数**（建议 81000 起），并先 `SELECT min(id),max(id) FROM tou_rule` 核对。

## 1.6 tou_rule 主键冲突（已修复 2026-10-11）

> **已修复**：anhui 改 51000、zhejiang 改 52000，两省各重跑两遍，现各 8 条；shandong 9 条、henan 5 条不变；Excel 已重导出（tou_rule 共 145 条）。`run.py` 写 tou_rule 前新增守卫：若同 id 已被其他区域占用则直接报错退出（`change TOU_ID_BASE`）。以下为问题原始记录。


`tou_rule.id` 是全局主键，`run.py` 用 `INSERT OR REPLACE` 写入；`anhui.py` 与 `shandong.py` 都用 `TOU_ID_BASE = 21000`，`zhejiang.py` 与 `henan.py` 都用 `22000`。后跑的区域会覆盖先跑区域的同 id 规则（`DELETE … WHERE region_code=? AND doc_id=?` 只删本区域，挡不住跨区域覆盖）。2026-10-11 DB 实测：

| 区域 | 应有规则 | DB 中实际 | 被谁覆盖 |
|---|---|---|---|
| anhui | 8（302号：3 条 normal + 2 条 sharp + 3 条 holiday，id 21001–21008） | **3**（仅 21006–21008 holiday） | shandong 21001–21005 |
| zhejiang | 8（21号 4 条 id 22001–22004 + 112号 4 条 id 22011–22014） | **4**（仅 112号 22011–22014） | henan 22001–22005 |

- `data/json/anhui.json`、`data/json/zhejiang.json`（生成于覆盖之前）仍各含 8 条，但 **DB 与 Excel（136 条规则）已丢失这些规则**，两者不一致。
- 各组的幂等哈希脚本（如 `work/north_hash.py`）按 `region_code` 过滤，**检测不到这种跨区域覆盖**。
- 修复建议（未执行）：把 `anhui.py` 改为 `TOU_ID_BASE = 25000`、`zhejiang.py` 改为 `26000`（或其他未占用基数）→ `DELETE FROM tou_rule WHERE region_code IN ('anhui','zhejiang')` → 依次 `python -m pipeline.run --region anhui …--skip-discover`、`--region zhejiang …`，再复跑 shandong、henan 确认二者规则数不变（9 / 5）→ `python -m pipeline.export_xlsx`。今后在幂等检查里加一条全局校验：`SELECT region_code, count(*) FROM tou_rule GROUP BY 1` 与预期对比。

## 1.7 运行环境

- Python 3（沙箱）。所需包：`pdfplumber pypdfium2 pytesseract numpy pillow xlrd openpyxl pandas`（`python-docx` 仅导出 Word 用）。**2026-10-11 本次核查时沙箱里只有 pillow，其余均缺失（沙箱重置会丢包，历次进度日志都记录过"lost numpy / reinstalled")**——每次开工先：
  ```bash
  python -m pip install pdfplumber pypdfium2 pytesseract numpy pillow xlrd openpyxl pandas
  ```
- 系统程序：`curl`（必须，HTTP 全走 curl）、`tesseract`（本机 5.5.0，系统 tessdata 只有 eng/osd）。**chi_sim 与 eng 训练数据随仓库放在 `pipeline/tessdata/`**，`ocr_table._tess` 通过 `--tessdata-dir` 使用，无需安装系统 chi_sim 包。`pandoc` 在 `/usr/bin/pandoc`。
- SQLite：`data/tariff.db` 为 **WAL** 模式（`PRAGMA journal_mode` = wal，持久化在库文件里）；`parse_common.connect()` 设置 `timeout=120`、`PRAGMA busy_timeout=120000`、`foreign_keys=ON`。最多 6 个 worker 曾并发写同一库；长时间 OCR 的 run 会持有写事务，其他进程需等待（广东组曾遇锁竞争）。

## 1.8 网络现实（沙箱出口为美国代理；2026-10-11）

| 目标 | 沙箱 curl | Hark 浏览器 | 备注 |
|---|---|---|---|
| 国网多数省站 `*.sgcc.com.cn`（tj、sc、jx、ln、jibei、nx、sn、gs、qh、xj、sd、hb、hn、ha、zj、ah、fj…） | ✗ 超时/空回复/代理 404 | 多数也 ✗（ERR_EMPTY_RESPONSE） | 宁夏例外：浏览器能打开并把 PDF 存入 `data/raw/ningxia/` |
| bj / hl / jl / md.sgcc.com.cn | HTTP 412（JS 反爬） | 部分可打开 | 蒙东 `/u/cms/md/picture/…jpg` 图片 curl 可下 |
| sh.sgcc.com.cn | 返回 2.4KB 瑞数 `$_ts` 挑战页 | 可打开 | `/html/files/*.xls` 附件 curl 可下 |
| js / he / sx / cq.sgcc.com.cn | ✓（he 偶发 curl rc 52 "Empty reply"） | — | 2026-10-11 复测 js、sx 均 200 |
| **95598.cn 列表** `https://www.95598.cn/osgweb/ipElectrovalenceStandard?partNo=P2019` | ✗（JS 渲染） | ✓（需选地区；偶有滑块） | 黑龙江列表"功能正在维护升级" |
| **95598.cn omg-static 文件** `https://www.95598.cn/omg-static/<id>` | ✓（2026-10-11 复测 200） | ✓ | 无扩展名，按魔数判断类型 |
| **南网 95598.csg.cn API** | ✓（2026-10-11 复测 POST 返回 `sta:"00"`） | — | 早期 south_west 笔记说 csg 不可达，已被 csg2 组推翻 |
| 内蒙古政府站 zxbq.gov.cn / xlgl.gov.cn | ✓ | — | impc.com.cn ✗ |
| fzggw.ah.gov.cn | 403 | — | 芜湖 whfgw.wuhu.gov.cn 附件需 Referer |
| pds.gov.cn（平顶山）、dehua.gov.cn（德化）、xunwu.gov.cn（寻乌，时好时坏）、shantou.gov.cn、jiangmen.gov.cn、fgw.ln.gov.cn、fzggw.nx.gov.cn、leshan.gov.cn | ✓ | — | |
| fgw.gxzf.gov.cn、各贵州/广西县政府站、fzgg.tj.gov.cn(403)、lflkq.lf.gov.cn、fgw.cxz.gov.cn(403)、fgw.qinghai.gov.cn、zj 政府站 | ✗ | 部分 ✗ | |

要点：
1. **HTTP 一律走 curl**（`parse_common.http_get`）：沙箱环境变量里的 HTTP_PROXY 会被 Python 客户端采用，对 js.sgcc.com.cn 返回 404；curl 直连。每次请求最多重试 2 次。
2. 国网省站多数不可达 → 主力渠道是 **95598.cn omg-static 副本**：列表需浏览器取 URL（写入 `seed.json`），文件 curl 下载。
3. 南网所有省份走 **95598.csg.cn queryInformationList API**（curl POST）。

## 1.9 `pipeline/sources.csv` 的状态

该登记表是早期调研产物，后来各组只更新了自己的行，**部分行已过时**：例如仍有 `hebei-north`（实际代码为 `jibei`）、陕西/甘肃/青海/新疆的 `history_ok=unknown`、上海 TOU "not re-verified"、江西文号 "46x号"、四川/重庆 "not found" 等。**以模块 docstring、`TOU_NOTES` 与本文档 §4 为准**；更新 sources.csv 列入待办（§6）。

---

# 2. 通用 SOP

## 2.1 新月份增量（每月例行）

**发布时间规律**（据 `announcements.json` 的 `publish_date` 统计）：原发公告集中在**上月 24–31 日**（个别次月 2–3 日：海南 2025-12 为 12-02 发布；山西 2026-03/04 更新版 2026-04-03 发布）；政府转载渠道滞后：福建德化转载在**当月 1–19 日**，蒙西正镶白旗转载在上月 28–31 日；95598 有时整批补发（青海 2025-11..2026-07 于 2026-07-10 补挂）。**建议轮询窗口：每月 25 日至次月 3 日查原发渠道，福建等转载渠道延长到次月 20 日。**

以新增 2026-11 为例：

```bash
cd /workspace/projects/55dbe641-d867-49ae-80e6-e219c2d53136
python -m pip install pdfplumber pypdfium2 pytesseract numpy pillow xlrd openpyxl pandas   # 沙箱重置后必做

# A. 有"实时发现"的区域（直接跑，会访问列表/API）
for r in jiangsu hebei-south shanxi chongqing mengxi fujian \
         guangdong-prd5 guangdong-jiangmen guangdong-huizhou guangdong-eastwest guangdong-north \
         shenzhen shenzhen-shenshan guangxi guizhou hainan yunnan; do
  python -m pipeline.run --region $r --from 2026-11 --to 2026-11
done
```

B. **seed 驱动的区域**（95598.cn 或其他需浏览器的列表）：先用 Hark 浏览器按 §3.1 打开 95598 P2019 列表，切到对应省，找到"YYYY年M月…代理购电…"条目，取 omg-static URL，按该省 `seed.json` 现有结构**追加一条 article**（字段见 §7.2）；安徽/浙江的 URL 写在模块内 `SEED` 字典里（无 seed.json）。然后：

```bash
python -m pipeline.run --region <code> --from 2026-11 --to 2026-11
```

C. 每个区域跑完：看 summary 的 `missing_announcement` / `parse_errors` / `validation_failures`；对 OCR 区域查 `announcements.json` 的 `ocr_corrections`，修复多的月份必须目视核对。

D. 全部完成后：

```bash
python -m pipeline.run --region <code> --from 2025-11 --to 2026-11 --skip-discover   # 全窗口复跑，刷新 JSON（窗口变了）
python -m pipeline.export_xlsx --from 2025-11 --to 2026-11
python -m pipeline.compare_energydc --region <code> --months 2025-11..2026-11          # 可选对账
```

注意：`build_json.build` 只导出 `--from/--to` 区间内的月份，增量只跑单月时 JSON 只含该月 —— **增量后务必用全窗口 `--skip-discover` 再跑一遍**以生成完整 JSON。

## 2.2 新增区域步骤

1. **定渠道**：按 §3 顺序找官方来源：省电力官网 → 95598.cn omg-static（国网）/ 95598.csg.cn API（南网）→ 省/市/县发改委或政府"公共企事业单位信息公开 › 供电"转载。energydc 只能用来确认"某月确有公告/大致标题"。
2. **建模块**：复制最接近的参考模块：
   - 文本 PDF + seed：`beijing.py`、`qinghai.py`（西北 util）、`jibei.py`（sgcc_cms seed）
   - xls：`shanghai.py`
   - HTML 表：`fujian.py`
   - 国网省站 CMS + OCR 海报：`hebei_south.py`、`shanxi.py`（自有 CMS）、`chongqing.py`（图集）
   - JPG/PNG 海报 OCR（seed）：`tianjin.py`、`hunan.py`/`shandong.py`（中部 util）
   - 市场化分时表：`ningxia.py`、`gansu.py`、`xinjiang.py`
   - 南网：`_guangdong_util.GDArea` 或 `_csg2_util`
3. **REGION**、`TOU_ID_BASE`（取未用基数，见 §1.5）、`TOU_NOTES`（写来源、政策文号/生效期/时段、口径、缺失原因）。
4. **seed.json**（如需）：`data/raw/<code>/seed.json`，结构参考同组省份。
5. **解析**：用 `sgcc_cms.parse_grid` / `_csg2_util.rows_from_table` 等按表头映射；分 3rd/4th 输配电价周期两套行布局（2026-08 起第四监管周期，见 §2.6）。
6. **校验**：实现 `extra_validate`（峰谷浮动公式），OCR 区域实现 `postprocess`（监管价多数投票）。
7. **分时规则**：从官方政策原文写 `tou_rules()`，用 `sgcc_cms.segs_from_hours` 生成段，`sgcc_cms.tou_doc` 生成 policy_doc（记录 `doc_no`、`source_url`）。
8. 跑两遍（第二遍 `--skip-discover`），比较哈希（§2.5）；目视核对至少一个 OCR 月。
9. 更新 `pipeline/README.md` 本区域小节、`pipeline/sources.csv` 行、本文档 §4。

## 2.3 全流程命令（单区域）

```bash
# 1 发现+下载+解析+入库+校验+导出JSON
python -m pipeline.run --region zhejiang --from 2025-11 --to 2026-10
# 2 离线复跑（幂等验证）
python -m pipeline.run --region zhejiang --from 2025-11 --to 2026-10 --skip-discover
# 3 看校验
cat data/validation_zhejiang.csv
# 4 对账（只读 DB，不入库）
python -m pipeline.compare_energydc --region zhejiang            # 需要时抓 energydc
python -m pipeline.compare_energydc --region zhejiang --no-fetch # 只用缓存
# 5 Excel
python -m pipeline.export_xlsx --from 2025-11 --to 2026-10
```

## 2.4 OCR 流程与交叉校验方法

**通用 OCR 流水线**（`pipeline/sources/ocr_table.py`）：图像（JPG/PNG，或 PDF 内最大嵌入位图 / pypdfium2 渲染）→ 二值化 → 找横线（同一左边界分组）成表 → 用表上方 chi_sim 标题选表（`pick_table`，排除 1.5 倍）→ 竖线/横线切格 → 数字格 tesseract（白名单 `0123456789.-`，放大，灰度+两种阈值多数表决 `ocr_num`）→ 合并单元格按列处理（无内部横线即合并，读一次赋给整段）→ 按表头文字映射列。中部组 `_central_util.find_tables` 用"长连续暗线"检测，抗彩色表头/水印。缓存：`data/raw/<code>/ocr_cache/<month>.json`，键 = 文件 sha256 + 模块 `OCR_VERSION`（改算法要 +1 才会重 OCR）。

**交叉校验（缺一不可）**：

1. **分量求和**：非分时 = 代理购电价格 + 线损 + 输配 + 系统运行费 + 基金 = 平段（市场化省份为各时段等式，如甘肃 10=1+4+7+8+9、11=2+5+7+8+9、12=3+6+7+8+9）。
2. **峰谷浮动公式**：峰/谷 = 平 ± k·B，B 因省而异（P、P+输配、P+线损、当月平均购电价格等），尖峰/深谷在峰/谷基础上再浮动；各省 k、B 见 §4。`ocr_table.solve_rows`：每个格子推出该行电度价的一个候选，取最大一致簇（≥2 个独立格子）定值，再修复不一致格子（写入 `ocr_corrections`）。中部组用 `affine_solve`：不假设系数，要求"各分时价 − 电度"在当月对输配电价呈仿射关系。
3. **监管价多数投票**：输配电价、需量、容量在同一输配电价监管周期内是常数（第三周期 ≤2026-07，第四周期 ≥2026-08）。
   - `ocr_table.regulated_consensus(parsed, anns, fields, period_key)`：同周期同(category,tariff_type,voltage,field)内，多数值（n≥2 且 > 半数）替换少数值，写日志。
   - ⚠ **默认 `period_key` = 当月行数**（`lambda m, rows: len(rows)`）。用默认值或显式按行数分周期的模块：`chongqing`、`hebei_south`、`gansu`、`xinjiang`、`hubei`（经 `_central_util.regulated_postprocess`）。问题：(a) 周期与行数不一定一一对应（如布局未变的天津/北京/黑龙江/福建）；(b) **该函数不区分文本月与 OCR 月，会覆盖文本 PDF 月份的值**——重庆的 2025-11、2026-03、2026-05 是 95598 文本 PDF，同样参与投票并可能被改。北方组因此另写了 `_north_util.consensus_ocr_only`（按 P3 <2026-08 / P4 ≥2026-08 分期，文本月参与投票但**永不被修改**），用于 heilongjiang、jilin、mengdong；shanxi、tianjin、mengxi、yunnan、shandong、henan、hunan 则显式传入按日期分期的 `period_key`。`liaoning.postprocess` 自带同类实现（按行数分期，仅两部制需量/容量；辽宁全为 OCR 月，无文本月被改的风险）。新模块若混有文本月与 OCR 月，**请用 `consensus_ocr_only` 或自带 is_ocr 判断**。
4. **信息表核对**（天津/重庆/河北南网/山西/吉林/蒙东：`INFO_ROWS = {"purchase_price":4,"line_loss":7,"system_op":8}`）：从同图的"代理购电价格信息表"读第 4/7/8 项核对共享分量。
5. **电压单调/输配排序**：高电压不应更贵；按行数选布局后，输配电价必须随电压下降（重庆、湖北、甘肃、新疆用输配电价列表逐行比对来认行）。

## 2.5 人工抽查与幂等

- **目视核对**：每个 OCR 区域至少一个月，对照原图逐格打字比对（脚本示例：`work/central/eyecheck.py`、`work/ln_eyecheck.py`、`work/tj_eyecheck.py`）；修复（`ocr_corrections`）多的月份优先核对（例：辽宁 2026-08 有 27 处修复，已目视 0 差异）。已核对月份记录在各省小节。
- **幂等两遍哈希**：同一区域连续两次运行（第二次 `--skip-discover`），对 `tariff_month`（按 month,category,tariff_type,voltage 排序）与 `tou_rule`（按 id）逐行 repr 做 sha256，两次必须相同；JSON 除 `generated_at` 外相同。参考脚本 `work/north_hash.py`：
  ```bash
  python work/north_hash.py anhui zhejiang shandong henan
  ```
  **补充全局检查**（防 §1.6 的跨区域覆盖）：
  ```bash
  sqlite3 data/tariff.db "select region_code,count(*),min(id),max(id) from tou_rule group by 1"
  ```

## 2.6 已知坑

1. **重庆 2026-02 OCR"互相抵消"错误**：OCR 把基金 0.047694 误读成 0.017694，残差修正步骤又把正确读出的线损 0.018634 改成 0.048634；两错相加不变，求和校验抓不到。已由 `chongqing.postprocess` 用守卫式硬修复改回（图像已核对，证据 `work/compare/evidence/cq2602_top.png` / `cq2602_bot.png`）。教训：残差修正触发时，应先与该省恒定的基金水平核对再改线损。
2. **河南 2026-10 孤立 NULL**：两部制 35千伏 高峰 OCR 留空，`affine_solve` 需要 ≥2 个独立格子才能补，结果存成 NULL，而 NULL 分时价不会触发校验。已由 `henan.postprocess` 守卫式补 0.959899295（取自官方 PDF，证据 `work/compare/evidence/henan_2026-10_table.png`）。教训：OCR 区域应额外检查"应印出却为 NULL"的格子。
3. **第四监管周期（2026-08 起）行布局变化**：几乎所有省份在 2026-08 增加单一制 110/220千伏 行或两部制 不满1千伏 行（依据 发改价格〔2026〕1077号 及各省文件，如 冀发改能价〔2026〕691号、青发改价格〔2026〕405号、黔发改价格〔2026〕421号、粤发改价格〔2026〕242号、鲁发改价格〔2026〕556号、云发改价格〔2026〕602号），需量/容量价也变。所有 OCR 模块按行数或月份选 `LAYOUTS`；新月份若再变布局，OCR 会失败或错位，需新增布局。
4. **1.5 倍表 / 转供电表 / 充换电设施表一律跳过**：选表时用标题排除（`EXCLUDE`、`avoid`），陕西 2026-05 起 PDF 多出 1.5 倍页、浙江有充换电表、河北南网海报有转供电两表。
5. **文本 PDF 的合并单元格**：pdfplumber 把合并格的值放在合并段中某一行（不一定首行）→ 用 `fill_shared`（西北）或 `parse_grid` 的前向填充。
6. **95598 列表标题与文件不符**：四川 95598 标"6月"的条目挂的是 7 月文件；寻乌转载"202603"实为 2025-09 表 → 解析时**必须核对文件内"执行时间"**（`check_month` / `EXEC_RE` / `effective_dates`）。
7. **同月多版本**：同月有"更正/修订/更新"时取最新发布（`sgcc_cms` 版本规则）；山西 2026-03/04 用 2026-04-03 的"调整后"版本；安徽 2026-08 只存月度表（07-29 发布），不存每周修订和实际结算表。
8. **voltage 文本截断**：江苏 2026-02/03/04/05 两部制 220 千伏行的 `voltage` 存成了 `220千伏及` / `220千伏及以`。**已修复（2026-10-11）**：`jiangsu._canon_voltage` 增加 220 千伏换行截断修复，删旧行后重跑，现 18 行均为 `220千伏及以上`。
9. **主键变动残留旧行**：见 §1.4。
10. **长时间 OCR 锁库**：多进程并发时注意 busy_timeout；必要时串行跑。
11. **山西下载大文件**（5–30MB）：写 `*.part` 再用 pypdfium2 校验后改名（曾出现截断的 2026-04 文件）。
12. **沙箱重置丢 Python 包、丢后台进程**：长 OCR 预热不要依赖后台进程；每步写进度日志（`work/progress_<group>.md`）。

---

# 3. 来源渠道手册

渠道优先级：① 省电力公司官网（能直连时）→ ② 国网 95598.cn omg-static 副本 / 南网 95598.csg.cn API → ③ 发改委/市县政府转载。同一月份多渠道时以"原发、文本层、最新版"优先，并核对文件内"执行时间"。

## 3.1 国网 95598.cn（网上国网 › 信息公开 › 执行的电价和收费标准）

- **列表页**：`https://www.95598.cn/osgweb/ipElectrovalenceStandard?partNo=P2019`（JS 渲染，curl 拿不到内容，必须用 Hark 浏览器）。
- **详情页**：`https://www.95598.cn/osgweb/ipSupportDetailItems?releaseInfoId=<id>&partNo=P2019`（预览里给出附件链接）。
- **文件 URL 模式**（静态，curl 可下）：
  - `https://www.95598.cn/omg-static/<数字id>`（**无扩展名**，按魔数判断：`%PDF`、`\xff\xd8` JPG、`\x89PNG`、`PK` docx/xlsx、`\xd0\xcf\x11\xe0` xls）
  - 也有 `https://www.95598.cn/omg-static//omg-static/<id>.pdf` 或 `…/omg-static/omg-static/<id>.pdf`（双段路径）、资讯类 `…/omg-static/<id>.html`。
- **浏览器步骤**（各浏览器笔记总结，`/workspace/web/notes/55dbe641-…/p2019-*/`）：
  1. 打开 `https://www.95598.cn/osgweb/index`，用页面顶部**地区选择器**切到目标省（内蒙古需选"内蒙 › 本级"对应蒙东）。
  2. 进入 信息公开 › 执行的电价和收费标准（即上面的 `ipElectrovalenceStandard?partNo=P2019`）。
  3. 用页面内搜索框搜"代理购电"/"YYYY年M月"/"M月"，或翻页（各省 4–5 页）。
  4. 打开详情，记录：标题、发布时间（publish_datetime）、releaseInfoId、附件名、omg-static URL；在 PDF 内核对"执行时间"。
  5. 写入 `data/raw/<code>/seed.json`（结构见 §7.2），或安徽/浙江写入模块 `SEED`。
- **风险与限制**：
  - **滑块验证码**："拖动下方拼图完成验证"。直接访问 `osgweb/pt/list.pt?partNo=P2019` 必出滑块且浏览器求解工具处理不了；**走 `ipElectrovalenceStandard?partNo=P2019` 路径通常不触发**。不要尝试破解验证码。
  - **黑龙江**：该列表对黑龙江显示弹窗"功能正在维护升级，暂时无法使用，建议您可通过网上国网APP使用相关功能"（2026-10-11），站内搜索最近的月度电价表只到 2022-07。只能从 95598 黑龙江首页新闻列表（最近 6 条）和资讯文章拿到部分月份。
  - 95598 偶有标题与附件错配（四川"6月"条目挂 7 月文件）、只给 .docx 且无静态 URL（四川 2026-09，JS blob 下载）、整批补挂（青海 2026-07-10）。
  - 每个 URL 最多重试 2 次。

## 3.2 南方电网 95598.csg.cn "南网在线"信息 API

- **接口**：`POST https://95598.csg.cn/ucs/ma/wt/searchService/queryInformationList`
- **请求头**：`Content-Type: application/json;charset=UTF-8`、`Origin: https://95598.csg.cn`、`Referer: https://95598.csg.cn/`
- **请求体**（精确）：
  ```json
  {"areaCode":"030000","level":"2","classId":"","keyword":"代理购电","pageNo":1,"pageSize":50,"version":"cn"}
  ```
- **areaCode**：`030000` 广东电网、`090000` 深圳供电局、`040000` 广西、`050000` 云南、`060000` 贵州、`070000` 海南。
- **返回**：`{"sta":"00","data":{"count":…,"infoList":[{"publishTime","classId","infoId","infoTitle","infoType","link"},…]}}`；翻页直到返回条数 < 50。
- **条目处理**：同一公告挂在 1–4 个栏目（classId）下，附件相同，只保留一个（优先 classId：广东 `c06263b20bf3`→`a2a9be101ac1`→`bcdbe6731d0b`；深圳 `8b69d39e1496`→`3868e1e1789f`→`8a98b1b6a916`→`09-help-elec`；广西 `4ead135257dc`；贵州 `a8d3146e14ed`；海南 `00ab16ad011b`）。
  - `link` 若含 `downloadFiles` → 直接就是 PDF：`https://95598.csg.cn/ucs/ma/wt/business/downloadFiles?documentId=…&documentType=pdf&documentName=downloadFile`。
  - 否则是静态页 `/ucs/sr/info/<省代码>/<classId>/<ts>/<infoId>.html`，在 HTML 中正则取 `href="https://95598.csg.cn/ucs/ma/wt/business/downloadFiles?…"`（PDF）及 `src="https://95598.csg.cn/ucs/sr/info/….(png|jpg)"`（预览图）。贵州 2025-12 某栏目页曾缺 PDF 链接 → 依次尝试各栏目。云南 2026-07/09 静态页只有一张长 PNG → 存 `YYYY-MM.png`。
- **标题正则**：广东 `广东电网有限责任公司关于(\d{4})年(\d{1,2})月代理购电工商业用户价格的公告`；深圳 `深圳供电局有限公司(\d{4})年(\d{1,2})月代理购电价格$`；广西 `^(\d{4})年(\d{1,2})月代理购电相关价格表$`；贵州 `贵州电网有限责任公司(\d{4})年(\d{1,2})月代理购电用户电价公告`；海南 `海南电网有限责任公司代理购电工商业用户电价表（(\d{4})年(\d{1,2})月）`；云南 `云南电网有限责任公司(\d{4})年(\d{1,2})月代理购电工商业用户电价表`。
- 手工试探：
  ```bash
  curl -s -X POST -H 'Content-Type: application/json' \
    -d '{"areaCode":"070000","level":"2","classId":"","keyword":"代理购电","pageNo":1,"pageSize":5,"version":"cn"}' \
    https://95598.csg.cn/ucs/ma/wt/searchService/queryInformationList | head -c 600
  ```
- 云南 API 只有 2026-06..10（关键词"代理购电/电价表/工商业用户电价/云南电网有限责任公司/空"都试过）。

## 3.3 省电力公司官网（CMS）

**国网通用 CMS**（`sgcc_cms.CMSRegion`）：列表 `/html/main/col<ID>/column_<ID>_<page>.html`；文章 `/html/main/col<ID>/YYYY-MM/DD/<ts>_1.html`（正文"发布日期："）；附件 `/html/files/YYYY-MM/DD/<ts>.<ext>`。版本规则：同月多篇取最新发布，标题含"更正|修订|修正|重新发布"者在同日胜出；同篇多文件取最新 ts。`live_list=False` 表示直接用 `seed.json`。

| 省站 | 栏目 / 列表 | 2026-10-11 可达性 | 用途 |
|---|---|---|---|
| 江苏 js.sgcc.com.cn（自有 .shtml CMS） | `http://www.js.sgcc.com.cn/xxgk/<供电公司>/djhsfbz/index.shtml`（+`index_N.shtml`），子目录顺序 gwntgdgs(南通)、gwnjgdgs、gwhagdgs、gwszgdgs、gwwxgdgs、gwczgdgs、gwycgdgs、gwlyggdgs | ✓ curl | 实时发现，取第一个带 PDF 的文章 |
| 河北南网 he.sgcc.com.cn | `http://www.he.sgcc.com.cn/html/main/col466/column_466_1.html` | ✓（偶发 rc 52） | 实时发现（排除"新能源/转供电主体"） |
| 山西 sx.sgcc.com.cn（自有 CMS） | `http://www.sx.sgcc.com.cn/wbw/channels/c40145_<N>.html` → `/homepage/wbw/articles/YYYYMM/a<ID>.html` → `/homepage/wbw/docs/YYYY/MM/DD/<hex>.pdf` | ✓ | 实时发现 |
| 重庆 cq.sgcc.com.cn（别名 cq.sgcc.cn） | `http://www.cq.sgcc.com.cn/html/main/col2680/column_2680_1.html` | ✓ | 实时发现（图集 JPG） |
| 上海 sh.sgcc.com.cn | `http://www.sh.sgcc.com.cn/html/main/col2017082164/column_2017082164_1.html` | 瑞数挑战页 | seed.json；附件 .xls curl 可下 |
| 宁夏 nx.sgcc.com.cn | 列 col502367（`…/html/main/col502367/…`，列表页 URL 推断【待核实】） | ✗ curl；浏览器可 | seed.json，PDF 由浏览器存盘 |
| 蒙东 md.sgcc.com.cn | `http://www.md.sgcc.com.cn/html/md/gb/xxgkx/zcfgjsfbz/index.shtml` | 412；图片可 curl | seed.json（浏览器取 img src） |
| 江西 jx / 四川 sc / 辽宁 ln / 天津 tj / 冀北 jibei | col2786 / col2804 / col3021 / col2774 / col40274 | ✗ | 只留作参考，实际走 95598 |
| 北京 bj / 吉林 jl / 黑龙江 hl | — | 412 | 走 95598 |
| 其余国网省站 | — | ✗ | 走 95598 |

## 3.4 政府转载（实际使用的）

| 区域 | 转载方 | URL | 用途 |
|---|---|---|---|
| 福建 | 德化县人民政府 › 政务公开 › 重点信息公开 › 公共企事业 › 供电 | 列表 `http://www.dehua.gov.cn/zwgk/zdxxgk/ggqsy/gd/`（标题/URL 在内联 JS 数组），文章 `…/gd/YYYYMM/tYYYYMMDD_NNNNNNN.htm`（HTML 表） | 全部 12 个月的数据来源（滞后 1–2 周） |
| 安徽 | 芜湖市发改委 通知公告 | `https://whfgw.wuhu.gov.cn/fgyw/tzgg/`（附件主机需 Referer，否则 nginx 403） | 2026-06..10 |
| 河南 | 平顶山市人民政府 › 价格收费 | `https://www.pds.gov.cn/channels/38390.html`（"…公示表-水印版.pdf"，seed 字段 `text_pdf_url`） | 2025-11..2026-08 文本 |
| 江西 | 寻乌县人民政府 › 公共企事业单位信息 › 供电领域 | xunwu.gov.cn（URL 在 `data/raw/jiangxi/seed.json`） | 2026-04、2026-08 |
| 蒙西 | 正镶白旗人民政府 › 通知公告 | `https://www.zxbq.gov.cn/eportal/ui?pageId=6cffcf6c91144e8a83ef0649e7e45da2&currentPage=N&moduleId=c92754c6ad4248a396dce21019855d4d&staticRequest=yes`，文章 `/zxbq/zwgk/zfxxgk/fdzdgknr/tzgg/<ts>/index.html` | 全部 12 个月 |
| 广东东西两翼 | 汕头市 | `https://www.shantou.gov.cn/cnst/ztzl/ggqsydw/gd/stgdj/fwxx/` | 仅交叉核对（11 个月 0 差异） |
| 分时政策原文转载 | 江门 jiangmen.gov.cn（粤331号）、石家庄 sjz.gov.cn（冀1115号）、宽城 hbkc.gov.cn（冀1275号）、宿州 ahsz.gov.cn（皖302号）、施甸 shidian.gov.cn（云924号）、锡林郭勒 fgw.xlgl.gov.cn（内1630号）、奉化 fh.gov.cn（浙21号） | 见 §4 各省 | tou_policy 的 source_url |

备用（未使用/不可达）：钦州 qinzhou.gov.cn 供电信息（广西 HTML 表，链接为 JS）、楚雄州发改委 fgw.cxz.gov.cn（云南转载，403）、贵州各县站（lp/leishan/gzss/dejiang/rongjiang/qdncg）、乌苏 xjws.gov.cn / 巴州 xjbz.gov.cn（新疆）、六安 luan.gov.cn（安徽，仅 1000px PNG）、廊坊 lflkq.lf.gov.cn（冀北，不可达）、jzs.gov.cn（河北南网镜像，见 sources.csv）。第三方建站 s21i.faiusr.com 的广西 PDF **不是官方，不得使用**。

## 3.5 energydc.cn（仅对账）

见 §5。公开 `/policy/<region>/<YYYY-MM>/<uuid>` 页与 `https://energydc.cn/edc/api/v1/eprice/policy/<uuid>` 访客接口可读；`/query` 历史月份需会员（门控，不得调用）；CSV/扫描包付费（不得购买）。sitemap：`https://energydc.cn/sitemap/sitemap_policy_1.xml`。

---

# 4. 分省 SOP

## 4.1 总览表（2026-10-11 DB 实测；窗口 2025-11..2026-10）

列说明：月=已入库月数/12；行=tariff_month 行数；TOU=tou_rule 条数；校验=`data/validation_<code>.csv` 失败行数；格式：T=文本 PDF/xls/HTML，O=OCR。

| 代码 | 名称 | 网 | 主来源 | 格式 | 月 | 行 | TOU | 校验 | 模块 |
|---|---|---|---|---|---|---|---|---|---|
| anhui | 安徽 | 国网 | 95598 omg-static + 芜湖发改委 | T | 12 | 90 | **3⚠** | 0 | anhui.py |
| beijing | 北京 | 国网 | 95598 P2019 | T | 12 | 84 | 3 | 13 | beijing.py |
| chongqing | 重庆 | 国网 | cq.sgcc 图集 + 95598 PDF | O(9)+T(3) | 12 | 99 | 2 | 0 | chongqing.py |
| fujian | 福建 | 国网 | 德化县政府转载 | T(HTML) | 12 | 108 | 2 | 0 | fujian.py |
| gansu | 甘肃 | 国网 | 95598 P2019 | O(9)+T(1) | **10** | 76 | 1 | 0 | gansu.py |
| guangdong-prd5 | 广东珠三角(五市→六市) | 南网 | 95598.csg API 030000 | T | 12 | 75 | 3 | 0 | guangdong_prd5.py |
| guangdong-jiangmen | 广东江门(不含恩平台山开平) | 南网 | 同上 | T | 12 | 75 | 3 | 0 | guangdong_jiangmen.py |
| guangdong-huizhou | 广东惠州 | 南网 | 同上 | T | 12 | 75 | 3 | 0 | guangdong_huizhou.py |
| guangdong-eastwest | 广东东西两翼 | 南网 | 同上 | T | 12 | 75 | 3 | 0 | guangdong_eastwest.py |
| guangdong-north | 广东粤北山区 | 南网 | 同上 | T | 12 | 75 | 3 | 0 | guangdong_north.py |
| shenzhen | 深圳 | 南网 | 95598.csg API 090000 表1-1 | T | 12 | 264 | 3 | 0 | shenzhen.py |
| shenzhen-shenshan | 深汕特别合作区 | 南网 | 同上 表1-2 | T | 12 | 75 | 3 | 0 | shenzhen_shenshan.py |
| guangxi | 广西 | 南网 | 95598.csg API 040000 | T | 12 | 90 | **0** | 21 | guangxi.py |
| guizhou | 贵州 | 南网 | 95598.csg API 060000 | T | 12 | 90 | 3 | 0 | guizhou.py |
| hainan | 海南 | 南网 | 95598.csg API 070000 | T | 12 | 81 | 2 | 0 | hainan.py |
| yunnan | 云南 | 南网 | 95598.csg API 050000 | O | **5** | 46 | 2 | 46 | yunnan.py |
| hebei-south | 河北南网 | 国网 | he.sgcc col466 | O | 12 | 111 | 4 | 0 | hebei_south.py |
| heilongjiang | 黑龙江 | 国网 | 95598 首页新闻 + 资讯 | T(6)+O(1) | **7** | 63 | 1 | 0 | heilongjiang.py |
| henan | 河南 | 国网 | 平顶山转载 + 95598 | T(10)+O(2) | 12 | 99 | 5 | 0 | henan.py |
| hubei | 湖北 | 国网 | 95598 P2019 | O | 12 | 90 | 3 | 0 | hubei.py |
| hunan | 湖南 | 国网 | 95598 P2019 | O | 12 | 99 | 3 | 0 | hunan.py |
| jiangsu | 江苏 | 国网 | js.sgcc 各供电公司栏目 | T(11)+O(1) | 12 | 132 | 7 | 0 | jiangsu.py |
| jiangxi | 江西 | 国网 | 95598 + 寻乌转载 | T | 12 | 90 | 7 | 0 | jiangxi.py |
| jibei | 冀北 | 国网 | 95598 P2019 | T | 12 | 93 | 7 | 0 | jibei.py |
| jilin | 吉林 | 国网 | 95598 P2019 | T(8)+O(4) | 12 | 75 | 1 | 0 | jilin.py |
| liaoning | 辽宁 | 国网 | 95598 P2019 | O | 12 | 78 | 2 | 0 | liaoning.py |
| mengdong | 蒙东 | 国网 | md.sgcc 内嵌扫描 + 95598 | O(10)+T(2) | 12 | 93 | 2 | 0 | mengdong.py |
| mengxi | 蒙西 | 内蒙古电力 | 正镶白旗政府转载 | O | 12 | 93 | 2 | 0 | mengxi.py |
| ningxia | 宁夏 | 国网 | nx.sgcc（浏览器存盘） | O | 12 | 90 | 2 | 21 | ningxia.py |
| qinghai | 青海 | 国网 | 95598 P2019 | T | 12 | 90 | 1 | 0 | qinghai.py |
| shaanxi | 陕西(不含榆林) | 国网 | 95598 P2019 | T | 12 | 99 | 3 | 0 | shaanxi.py |
| shaanxi-yulin | 陕西榆林地区 | 国网 | 同一 PDF | T | 12 | 99 | 3 | 0 | shaanxi_yulin.py |
| shandong | 山东 | 国网 | 95598 P2019 | O | 12 | 90 | 9 | 0 | shandong.py |
| shanghai | 上海 | 国网 | sh.sgcc（seed）xls | T(xls) | 12 | 147 | 9 | 0 | shanghai.py |
| shanxi | 山西 | 国网 | sx.sgcc c40145 | O | 12 | 90 | 9 | 0 | shanxi.py |
| sichuan | 四川 | 国网 | 95598 P2019 | T | **11** | 81 | 4 | 0 | sichuan.py |
| tianjin | 天津 | 国网 | 95598 P2019 JPG | O | 12 | 120 | 3 | 0 | tianjin.py |
| xinjiang | 新疆 | 国网 | 95598 P2019 海报 | O | 12 | 90 | 4 | 0 | xinjiang.py |
| xizang | 西藏 | 国网 | 无月度序列 | — | 0 | 0 | 2 | 0 | xizang.py |
| zhejiang | 浙江 | 国网 | 95598 omg-static | T | 12 | 123 | **4⚠** | 5 | zhejiang.py |

合计：tariff_month 3713 行，tou_rule 145 条（修复主键冲突后），policy_doc 499 条（agency_price 451、tou_policy 48）。（anhui、zhejiang 规则数已在修复后恢复为各 8 条；上表若显示 3/4 条为修复前快照。）

**统一运行命令**（下文各省不再重复，除非有特殊参数）：
```bash
python -m pipeline.run --region <code> --from 2025-11 --to 2026-10            # 发现+全流程
python -m pipeline.run --region <code> --from 2025-11 --to 2026-10 --skip-discover   # 离线复跑
```

**各省模板字段**：来源/入口 · 发现方式 · 文件格式 · 解析要点 · 分时政策 · 校验规则 · 覆盖与缺失 · 已知问题/待决 · 文件路径。

---

## 4.2 华北

### 北京 `beijing`
- **来源/入口**：95598.cn P2019（地区 北京）；bj.sgcc.com.cn 对 curl 返回 412，无稳定列表。
- **发现**：`data/raw/beijing/seed.json`（浏览器采集 12 条 omg-static PDF）。
- **格式**：3 页文本 PDF：p1 代理购电工商业用户电价表（入库）、p2 1.5 倍（跳过）、p3 信息表。
- **解析**：pdfplumber p1。7 行（窗口内不变，第四周期未改布局）：单一制 不满1千伏 / 1-10千伏 / 35-110千伏 / 220千伏及以上；两部制 1-10千伏 / 35-110千伏 / 220千伏及以上。需量/容量为整数（51/32 等）。尖峰 1、7、8、12月印出；无深谷。价格存 6 位小数（官方 2026-10 单一制 1-10 峰 0.99322375 → 存 0.993224，属取整约定）。
- **分时**：京发改规〔2023〕11号（2023-08-18，2023-09-01 起），https://fgw.beijing.gov.cn/fgwzwgk/2024zcwj/bwgfxwj/202308/t20230821_3718725.htm 。峰 10-13、17-22；平 7-10、13-17、22-23；谷 23-7；尖峰 夏(7、8月) 11-13、16-17，冬(1、12月) 18-21。3 条规则。尖峰仅适用 ≥100kVA 且 ≥1千伏用户（表内不满1千伏也印尖峰，照存）。
- **校验**：浮动仅作用于代理购电价格；峰平谷比 不满1千伏单一制 1.71:1:0.36，≥1千伏单一制 1.8:1:0.3，两部制 1.6:1:0.4；尖峰 = 峰 ×1.2。13 条 `voltage_monotonic` 失败是**真实特征**（单一制 1-10千伏 峰/尖峰 > 不满1千伏，因比价不同）。
- **覆盖**：12/12。
- **路径**：`pipeline/sources/beijing.py`、`data/raw/beijing/`。

### 天津 `tianjin`
- **来源**：95598.cn P2019（地区 天津）；tj.sgcc.com.cn 不可达，fzgg.tj.gov.cn 403。
- **发现**：`data/raw/tianjin/seed.json`（`live_list=False`；seed 有 `duplicates` 字段记录 2026-07 重复条目）。
- **格式**：每月一张 JPG 海报（3543×7087，2025-12 为 4961×9921，"国家电网"水印重），含主表（入库）+ 1.5 倍表（跳过）+ 价格信息表（仅核对 P/线损/系统运行费）。
- **解析**：OCR（`ocr_table`，缓存 `ocr_cache/`，OCR_VERSION 1）。10 行/月：单一制/两部制 × 不满1千伏、1-10千伏、35千伏-110千伏(不含)、110千伏-220千伏(不含)、220千伏及以上。8 位小数（基金 0.02716875）。尖峰仅两部制、仅 1、2、7、8、12月印出；单一制 "-" → NULL。无深谷。空白需量/容量格逐格重读（2026-09/10 两部制 1-10千伏 容量 27.0）。
- **分时**：津发改价管〔2025〕277号（2025-08-06，2025-10-01 起），官方 PDF https://www.95598.cn/omg-static//omg-static/99309111418199724687701554929817.pdf （副本 `web/downloads/tianjin_tou_277.pdf`），亦见 fzgg.tj.gov.cn …/202509/t20250902_7124414.html。7、8月 峰 15-23（尖峰 20-22）平 0-1、9-15、23-24 谷 1-9；其他月 峰 8-10、16-22（1、2、12月 尖峰 18-20）平 6-8、10-12、14-16、22-24 谷 0-6、12-14。3 条规则。
- **校验**：非分时 = Σ分量 = 平；D = round(0.6·(P+输配), 6)；峰/谷 = 平 ± D；尖峰 = 峰 + round(0.32·B, 6)；`extra_validate` 容差 5e-9（逐行精确到 8 位）。输配/需量/容量按周期（P3 ≤2026-07 / P4 ≥2026-08）多数投票。
- **覆盖**：12/12；2025-12、2026-09 目视 0 差异。
- **路径**：`pipeline/sources/tianjin.py`、`data/raw/tianjin/`。

### 河北南网 `hebei-south`
- **来源**：`http://www.he.sgcc.com.cn/html/main/col466/column_466_1.html`（"供电企业电价和收费标准"），标题 `国网河北省电力有限公司YYYY年M月代理购电价格…`，排除"新能源|转供电主体"。
- **发现**：实时（`sgcc_cms.CMSRegion`，max_pages 6，timeout 60）。主机偶发 "Empty reply"（curl rc 52），靠 2 次重试。
- **格式**：单页 PDF，只含一张 3766px 嵌入海报（无文本层，浅水印），5 张表：代理购电工商业用户电价表（入库）、1.5 倍、信息表、转供电终端用户表、转供电 1.5 倍（均不入库）。
- **解析**：OCR（OCR_VERSION 2）。≤2026-07：单一制 5（不满1/1~10/35/110/220及以上）+ 两部制 4（无不满1）= 9 行；≥2026-08（冀发改能价〔2026〕691号）两部制加不满1千伏 = 10 行，需量/容量变为 36.8/35.2/33.6/32/30.4 与 23/22/21/20/19。**尖峰、深谷每行每月都印**（含不满1千伏和非对应季节）→ 照印存。
- **分时**：冀发改能价〔2025〕1115号（2025-10-14，2025-11-01 起，覆盖全窗口），官方扫描件由石家庄发改委转载（`TOU_1115` URL，副本 `web/downloads/hebei_south_tou_1115.pdf`）。春(3-5) 深谷 12-15、谷 3-7、11-12、平 0-3、7-11、15-16、峰 16-24；夏(6-8) 谷 1-7、12-14、平 0-1、7-12、14-16、峰 16-19、22-24、尖峰 19-22；秋(9-11) 深谷 12-14、谷 2-6、11-12、14-15、平 0-2、6-11、15-16、峰 16-24；冬(12-2) 谷 2-6、11-15、平 0-2、6-7、9-11、15-17、23-24、峰 7-9、19-23、尖峰 17-19。4 条规则。
- **校验**（仅用于修 OCR）：平 = 非分时；峰/谷 = 平 ± 70%·当月平均购电价格（每月一个常数）；尖峰 = 峰 + 20%·1.7·base；深谷 = 谷 − 20%·0.3·base。`postprocess` = `regulated_consensus`（tdp/需量/容量，**默认按行数分期**）。
- **覆盖**：12/12；2025-11、2026-08 目视核对。
- **已知问题**：对账 26 行差异均判为 energydc 错（2026-03/04/05/09）。
- **路径**：`pipeline/sources/hebei_south.py`、`data/raw/hebei-south/`（含 `ocr_cache/`）。

### 冀北 `jibei`
- **来源**：95598.cn P2019（地区 冀北）。自有栏目 `http://www.jibei.sgcc.com.cn/html/main/col40274/column_40274_1.html` 只有滚动覆盖的"（当期）/（历史）"文章且不可达；镜像 lflkq.lf.gov.cn 不可达。
- **发现**：`data/raw/jibei/seed.json`（`live_list=False`，标题正则 `(\d{4})年(\d{1,2})月账期冀北代理购电价格表`）。
- **格式**：文本 PDF；p1 附表1 主表（入库），附表2 1.5 倍、附表3 价格表、附表4 转供电跳过。
- **解析**：pdfplumber `parse_grid`。列：电度 | P、线损、输配、系统运行、基金 | 尖峰、峰、平、谷（2026-01 起有深谷）| 需量、容量。≤2026-07 7 行（两部制也有不满1千伏行）；≥2026-08 10 行。尖峰/深谷照印存（2026-10 在政策外月份也印了，照存）。
- **分时**：2025-11..12 冀发改能价〔2023〕1711号（按 2025-11 表注3 转述，source https://www.95598.cn/omg-static/99306111884397824210202129032439；effective_from 为空）：夏 6-8 尖峰 17-20；冬 11、12、1 尖峰 17-19、谷 1-7、12-14；其他 峰 8-10、16-22 谷 1-7、12-14。2026-01-01 起 冀发改能价〔2025〕1275号（2025-11-26，宽城县政府转载 https://www.hbkc.gov.cn/art/2025/12/4/art_5989_1095355.html）：6-8 尖峰 18-21；1、11、12 尖峰 17-19 深谷 12-15 谷 1-6；2月 深谷 12-15 谷 1-6 峰 16-22；3-5、9、10 谷 1-6、12-15 峰 16-22；不满1千伏 不执行尖峰/深谷。共 7 条。
- **校验**：base = 当月平均购电价格（平段）：峰/谷 ±70%，尖峰 = 峰 +20%，深谷 = 谷 −20%；线损/输配/系统运行/历史偏差/基金不浮动。
- **覆盖**：12/12。
- **路径**：`pipeline/sources/jibei.py`、`data/raw/jibei/`。

### 山西 `shanxi`
- **来源**：`http://www.sx.sgcc.com.cn/wbw/channels/c40145_<N>.html`（信息公开 › 收费标准，混有现货运营费用/输配电价表），文章 `/homepage/wbw/articles/YYYYMM/a<ID>.html`，附件 `/homepage/wbw/docs/YYYY/MM/DD/<hex>.pdf`。curl 可直连。
- **发现**：实时（自有 CMS 解析，MAX_PAGES 6，标题 `国网山西省电力有限公司关于(\d{4})年(\d{1,2})月代理购电价格`）；2026-03/04 取 2026-04-03 "…代理购电价格更新"（文件"…价格表-调整后(3).pdf"）。
- **格式**：单页 PDF 内嵌 4109×5760 海报（无文本层、浅水印），三表：主表（入库）、1.5 倍（跳过）、信息表（核对 P/线损/系统运行）。下载大且慢（5–30MB）：先写 `*.part`，pypdfium2 校验后改名。
- **解析**：OCR（OCR_VERSION 3；预清洗、按连续线段过滤水印笔画、TOU 格对齐到印出的月度 D）。≤2026-04 12 列（无深谷）；≥2026-05 13 列（加深谷列）。行：P3 7 行（单一制 不满1/1-10（20）/35，两部制 1-10（20）/35/110/220及以上）；P4 9 行（单一制加 110、220及以上），需量/容量变化。
- **分时**：≤2026-04-30 晋发改商品发〔2021〕479号（+〔2023〕166号），时段按公告注2转述（source 用国网山西 2025-10-28 PDF）：峰 8-11、17-23，平 7-8、13-17、23-24，谷 0-7、11-13；尖峰 18-20 于 1、7、8、12月 仅两部制。2026-05-01 起 晋发改商品发〔2026〕15号（2026-01-31，2026-02-28 发布）https://fgw.shanxi.gov.cn/sxfgwzwgk/sxsfgwxxgk/xxgkml/tz/202602/t20260228_10069158.shtml：春(3-5) 峰 6-8、17-24 谷 2-5、9-15 平 0-2、5-6、8-9、15-17；夏(6-8) 峰 6-8、18-24（尖峰 18-21）谷 2-5、10-15 平 0-2、5-6、8-10、15-18；秋(9-11) 峰 6-8、17-24 谷 2-5、11-15 平 0-2、5-6、8-11、15-17；冬(12-2) 峰 6-8、16-23（尖峰 17-20）谷 2-5、10-15 平 23-2、5-6、8-10、15-16；春节/劳动节/国庆 深谷 13-15（holiday 规则，2/5/10月）。共 9 条。
- **校验**：平 = 电量电价 = Σ分量；峰/谷 = 平 ± 60%/55%·当月平均购电价格；尖峰 = 峰 + 20%·1.6·base；深谷 = 谷 − 20%·0.45·base（仅修 OCR，存印出值，"—" → NULL）。`postprocess` = `regulated_consensus`（显式按日期分期 `_period`）。
- **覆盖**：12/12；2025-12、2026-08 目视 0 差异；对账第二轮对 2026-02..07 共 84 格逐格目视，全部我方正确。
- **路径**：`pipeline/sources/shanxi.py`、`data/raw/shanxi/`。

### 蒙西 `mengxi`（内蒙古电力集团）
- **来源**：impc.com.cn 不可达；正镶白旗人民政府 通知公告每月转载（来源 正镶白旗供电分公司）。列表 `https://www.zxbq.gov.cn/eportal/ui?pageId=6cffcf6c91144e8a83ef0649e7e45da2&currentPage=N&moduleId=c92754c6ad4248a396dce21019855d4d&staticRequest=yes`，标题 `内蒙古电力（集团）有限责任公司代理购电工商业用户电价表（执行时间：YYYY年M月）`。
- **发现**：实时（curl）。文章正文 3–4 张图片（顺序不定；2026-06 起含 1.5 倍表；2026-07 主表出现两次不同分辨率）→ OCR 每张图标题选主表（含"代理购电工商业用户电价表"、不含 1.5 倍，取最大图），存 `data/raw/mengxi/YYYY-MM.<ext>`，全部图片在 `img/`，选择记录 `selection.json`。
- **解析**：`ocr_table.solve_rows`，base_kind='month'（只有 P 浮动）。列：电度 | P、线损、输配、系统运行、基金 | [尖峰] 峰 平 谷 [深谷] | 需量 容量；尖峰/深谷列仅 6-8月（小风季）印出（所有行含不满1千伏）。P3 7 行（单一制 不满1/1~10（20）/35 + 两部制 1~10（20）/35/110/220及以上）；P4 10 行（各 5）。
- **分时**：内发改价费字〔2023〕1630号（2023-12-28，2024-02-01 起），锡林郭勒盟发改委转载 https://fgw.xlgl.gov.cn/fgw/ywgz/zdxm/tzgg/2024010409440178739/index.html 。大风季(1-5、9-12) 峰 6-8、18-22 平 4-6、8-11、16-18、22-24 谷 0-4、11-16；小风季(6-8) 峰 6-8、18-22 平 0-6、8-11、16-18、22-24 谷 11-16，尖峰 19-21，深谷 13-15。2 条规则。
- **校验**：大风季 峰/谷 = 平 ± 68%/52%·P；小风季 ±54%/56%·P，尖峰 = 平 + 84.8%·P，深谷 = 平 − 64.8%·P。合并格 P 误读时用 (峰−平)/k 反推（2026-08 读成 '10.3234'），再由求和推系统运行费。监管价按周期多数投票（`_period`）。
- **覆盖**：12/12；2025-12、2026-07、2026-10 目视 0 差异。
- **路径**：`pipeline/sources/mengxi.py`、`data/raw/mengxi/`（`html/`、`img/`、`ocr_cache/`）。

### 蒙东 `mengdong`
- **来源**：md.sgcc.com.cn 信息公开 › 政策法规及收费标准 `http://www.md.sgcc.com.cn/html/md/gb/xxgkx/zcfgjsfbz/index.shtml`（412 给 curl；每篇文章一张 2245px 内嵌扫描 `/u/cms/md/picture/YYYYMM/<hash>.jpg`，curl 可下）；95598 P2019（内蒙 › 本级）仅列 2026-07..10。
- **发现**：`data/raw/mengdong/seed.json`（2025-11..2026-06 md.sgcc 图片，2026-07..10 95598 PDF；07/08 为图片 PDF，09/10 有文本层）。
- **解析**：OCR 10 个月 + pdfplumber 2 个月。列 11 或 13（含尖峰/深谷时）。P3 7 行；P4 10 行（单一制/两部制 × 不满1/1-10(20)/35/110(66)/220及以上）。尖峰/深谷 6-8月印出，"-"/空 → NULL。
- **分时**：内发改价费字〔2023〕1631号《关于完善蒙东电网工商业分时电价政策的通知（试行）》（2023-12-28，2024-02-01 起），官方扫描 https://www.95598.cn/omg-static/99308051060403522315301243350380 （副本 `web/downloads/mengdong_tou_1631.pdf`；适用范围见 内发改价费字〔2024〕143号，`web/downloads/mengdong_tou_143.pdf`）。峰 6-9、17-22，平 5-6、9-11、14-17、22-24，谷 0-5、11-14；6-8月 尖峰 18-20、深谷 12-14。2 条规则。
- **校验**：只 P 浮动：峰 = 平 + 68%·P，谷 = 平 − 52%·P，尖峰 = 峰 + 20%·1.68·P，深谷 = 谷 − 20%·0.48·P（6 位）。`postprocess` = `consensus_ocr_only`。
- **覆盖**：12/12；2025-11、2026-07 目视。
- **路径**：`pipeline/sources/mengdong.py`、`data/raw/mengdong/`。

---

## 4.3 东北

### 辽宁 `liaoning`
- **来源**：95598.cn P2019（地区 辽宁）；ln.sgcc.com.cn col3021（"代理购电价格（YYYY年M月）"）不可达。
- **发现**：`data/raw/liaoning/seed.json`（`live_list=False`）。
- **格式**：扫描 PDF：p1 主表图、p2 信息表图。
- **解析**：OCR（`ocr_table.find_tables/read_table` + `solve_rows`，OCR_VERSION 4）。辽宁不印 35千伏 行。P3 6 行：单一制 不满1 / 1~10（20）/ 110（66），两部制 1~10（20）/ 110（66）/ 220及以上；P4（发改价格〔2026〕1077号）8 行：两类 × 不满1 / 1~10（20）/ 110（66）/ 220及以上。尖峰仅 1、7、8、12月印出；无深谷。
- **分时**：辽发改价格〔2023〕441号（2023-08-25，2023-09-01 起），https://fgw.ln.gov.cn/fgw/index/tzgg/2023083014061818174/index.shtml （文号依据辽宁发改委《现行有效行政规范性文件目录（2024年版）》）。峰 7:30-10:30、16-21；谷 11:30-12:30、22-5；其余平；尖峰 17-19 于 1、7、8、12月。2 条规则。2025 年辽宁新文件（居民电采暖 898号、煤改电）不涉工商业。2025-03 起现货连续运行，表内分时价为参考价。
- **校验**：Σ分量 = 非分时 = 平；峰/谷 = 平 ± 50%·P；尖峰 = 峰 + 25%·1.5P（= 平 + 87.5%·P）。需量/容量同周期多数投票（行数分期）。
- **覆盖**：12/12；2026-01、2026-08（27 处修复）、2026-09 目视 0 差异。OCR 修复总数 96。
- **路径**：`pipeline/sources/liaoning.py`、`data/raw/liaoning/`。

### 吉林 `jilin`
- **来源**：95598.cn P2019（地区 吉林）；jl.sgcc.com.cn 412、收费标准栏目只零星月份。
- **发现**：`data/raw/jilin/seed.json`。
- **格式**：2025-11..2026-05 与 2026-08 为 2 页文本 PDF；2026-06、07、09、10 为 2245px 渲染 JPG（OCR）。
- **解析**：列序（按表头）：电度、P、线损、系统运行费、输配、基金、尖峰/峰/平/谷、需量、容量。尖峰恒为 "-"（灵活启动）→ NULL；无深谷。P3 6 行（单一制 不满1/1-10/66千伏 + 两部制 1-10/66/220及以上）；P4（吉发改价格〔2026〕399号 / 发改价格〔2026〕1077号）单一制加 220及以上 → 7 行。
- **分时**：吉发改价格〔2025〕11号（2025-01-16，2025-02-01 起；代理购电用户 2025-04-01 起），官方扫描 https://www.95598.cn/omg-static/99305070811275452078400030458563.pdf （副本 `web/downloads/jilin_tou_2025_11.pdf`）。全年一条：峰 8-10、16-21；平 5-8、10-11、14-16、21-23；谷 0-5、11-14、23-24。无尖峰规则（灵活启动）。
- **校验**：峰/谷 = 平 ± 55%·P（7 位小数）；`postprocess` = `consensus_ocr_only`（文本月参与投票不被改）。
- **覆盖**：12/12；2026-07、2026-10 目视。
- **路径**：`pipeline/sources/jilin.py`、`data/raw/jilin/`。

### 黑龙江 `heilongjiang`
- **来源**：95598.cn **P2019 列表对黑龙江维护中**（"功能正在维护升级"）；hl.sgcc.com.cn 412，两个电价栏目止于 2025-03（新闻中心 › 供电企业执行的电价和标准）/ 2025-08（营销服务 › 同名）。实际：95598 黑龙江首页新闻列表（最近 6 条，2026-05..10 文本 PDF）+ 资讯文章 https://www.95598.cn/omg-static/99312281801161322062502015813888.html （2026-01，附表1/附表2 两张 JPG）。
- **发现**：`data/raw/heilongjiang/seed.json`（含 `missing` 字段）。
- **解析**：9 行（窗口内不变）：单一制 不满1/1-10(20)/35/110(66)/220及以上，两部制 1-10(20)/35/110(66)/220及以上。尖峰列空（暂停执行）→ NULL；无深谷。2026-01 OCR（OCR_VERSION 2）。
- **分时**：黑发改价格函〔2024〕291号（2024-10-12 印发，2025-01-01 起），官方扫描 https://www.95598.cn/omg-static//omg-static/99301061503136265824901857003111.pdf （副本 `web/downloads/heilongjiang_tou_291.pdf`）。峰 7-8、9-11:30、15:30-20；谷 12-14、23:30-5:30；其余平；尖峰暂停。1 条。
- **校验**：只 P 浮动，峰/谷 = 平 ± 50%·P（精确到 6 位）。`postprocess` = `consensus_ocr_only`。
- **覆盖**：**7/12**（2026-01、2026-05..10）。**缺 2025-11、2025-12、2026-02、2026-03、2026-04**：列表维护、官网栏目止于 2025-08、未找到官方转载。2026-01 目视 0 差异。
- **待办**：定期复查 P2019 黑龙江列表是否恢复；如恢复，按 §3.1 补 seed 即可。
- **路径**：`pipeline/sources/heilongjiang.py`、`data/raw/heilongjiang/`；浏览器笔记 `/workspace/web/notes/55dbe641-…/p2019-heilongjiang-*/blocker_evidence.md`。

---

## 4.4 华东

### 上海 `shanghai`
- **来源**：`http://www.sh.sgcc.com.cn/html/main/col2017082164/column_2017082164_1.html`（标题 `上海市YYYY年M月代理购电公示表/价格表`），HTML 被瑞数 `$_ts` 反爬拦截。
- **发现**：`data/raw/shanghai/seed.json`（浏览器一次性采集）；`/html/files/*.xls` 附件 curl 可下。同月多文件取最新 ts（2026-02 曾于 2026-02-02 静默替换 xls）。
- **格式**：.xls 三个 sheet：表2 信息表、表1 主表（入库）、表3 1.5 倍（跳过）。xlrd 读取。
- **解析**：category 用表内"用电分类"原文：≤2026-07 一般工商业用电（单一制 3 + 两部制 5）+ 大工业用电 两部制 5 = 13 行；≥2026-08（发改价格〔2026〕1077号）工商业用电 单一制 5 + 两部制 5 = 10 行。深谷印出（大工业 / 2026-08 起全部两部制）；尖峰 1、7、8、12月 两部制印出。
- **分时**：沪发改价管〔2022〕50号（2022-12-16，2023-01-01 起）https://fgw.sh.gov.cn/fgw_jggl/20221216/e2652e3ab7ee49438d6e82af8880b160.html （时段按每月 xls 注2 转述；tou_rule 为两部制时段；一般工商业单一制分时用户为两段制 峰 6-22、谷 22-6，未存）+ 沪发改价管〔2024〕28号（2024-06-13，2024-09-01 起，大工业深谷）https://fgw.sh.gov.cn/fgw_jggl/20240613/c753f8153e15492898c2ea3737ee93d5.html ：元旦/春节/清明/劳动/端午/中秋/国庆及 2-6、9-11月休息日 0-6、22-24 深谷（平段基价 −80%）。尖峰 7-8月 12-14、1、12月 19-21（仅两部制）。9 条（normal 4、holiday 3、weekend 2）。
- **校验**：B = P + 输配；两部制 夏(7-9)/冬(1、12) 峰 +80% 谷 −60%，其余月 峰 +60% 谷 −50%；单一制 夏冬 峰 +20% 谷 −45%，其余 峰 +17% 谷 −45%；尖峰 = 峰 +25%。
- **覆盖**：12/12；对账 147/147 行 0 差异。
- **路径**：`pipeline/sources/shanghai.py`、`data/raw/shanghai/`。

### 江苏 `jiangsu`
- **来源**：`http://www.js.sgcc.com.cn/xxgk/<供电公司>/djhsfbz/index.shtml`（+`index_N.shtml`），各市公司都转发省公告；南通 `gwntgdgs` 带 PDF，淮安/苏州常只有 JPG 扫描。标题 `关于YYYY年M月代理购电工商业用户电价的公告`。PDF URL 形如 `/images/xxgk/<co>/djhsfbz/YYYY/MM/DD/<id>`。
- **发现**：实时，按 `SUBFOLDERS` 顺序（gwntgdgs, gwnjgdgs, gwhagdgs, gwszgdgs, gwwxgdgs, gwczgdgs, gwycgdgs, gwlyggdgs），每月取第一个带 PDF/doc 的文章（MAX_LIST_PAGES 6）。
- **格式**：文本 PDF，附表2 在第 4 页；2025-12 为扫描件 → OCR（tesseract eng、数字白名单，网格检测后逐格；行标签借用相邻月 `make_template`，输配电价必须逐行匹配，否则拒绝 OCR；监管需量/容量与模板不一致时以模板为准，记入 `announcements.json`）。
- **解析**：category 承载 100 千伏安分类（用户决策）：`工商业用电`（两部制）、`工商业用电(100千伏安及以上)`、`工商业用电(100千伏安以下)`（单一制）。2026-08 起（发改价格〔2026〕1077号 / 苏发改价格发〔2026〕777号）单一制增 110、220千伏及以上，10 → 14 行/月。尖峰列在 12–1 月与 7–8 月出现。深谷附表2 不印 → NULL。
- **分时**：苏发改价格发〔2025〕426号（2025-04-30，2025-06-01 起）http://fzggw.jiangsu.gov.cn/art/2025/4/30/art_284_11557001.html 。7 条：春秋(3-5、9-11) / 2、6月 / 7-8月（含尖峰 14-15、19:30-21:30）/ 12-1月（尖峰 18-20）+ 节假日深谷（春节/五一/国庆 10-14，仅 315kVA 及以上工业）。尖峰仅 315kVA 及以上工业；其他用户尖峰时段按峰计。
- **校验**：峰上浮（对购电价）两部制 80%、单一制≥100kVA 70%、<100kVA 60%；谷下浮 65%；`extra_validate` 容差 0.0006。
- **覆盖**：12/12；对账 132/132 0 差异。
- **已知问题**：**2026-02、03、04、05 两部制 220 千伏行 `voltage` 被截断为 `220千伏及`/`220千伏及以`**→ **已修复（2026-10-11）**。对账中 14 个 `ours_null` 尖峰判为 energydc 合并单元格扩散。
- **路径**：`pipeline/sources/jiangsu.py`、`data/raw/jiangsu/`。

### 浙江 `zhejiang`
- **来源**：95598.cn omg-static（P2019 地区 浙江），标题"国网浙江省电力有限公司关于YYYY年M月代理工商业用户购电价格的公告"；zj.sgcc 无列表，浙江政府站（luqiao/hangzhou/fzggw.zj）从沙箱超时。
- **发现**：模块内 `SEED` 字典（月 → omg-static id + 发布日，URL 前缀 `https://www.95598.cn/omg-static/`），浏览器笔记 `/workspace/web/notes/55dbe641-…/95598_zj_ah-*/`。
- **格式**：6–7 页文本 PDF：pp1-3 公告正文，p4 表1 主表（入库），表2 1.5 倍、表3 充换电设施（跳过），表4 信息表。
- **解析**：≤2026-06：两部制 `大工业用电` ×4 + 两部制 `一般工商业用电` ×4 + 单一制 `一般工商业用电` ×3 = 11 行；2026-07：两部制 `大工业及一般工商业用电` ×5（含不满1千伏）+ 单一制 ×3 = 8 行；≥2026-08：两部制 ×4 + 单一制 ×5 = 9 行。深谷仅节假日月份印出（2026-02、2026-05、2026-10），"/" → NULL。不满1千伏行系统运行费不同（合并格前向填充）。
- **分时**：≤2026-06-30 浙发改价格〔2024〕21号（2024-03-01 起；source 用奉化区转载 https://www.fh.gov.cn/art/2024/2/2/art_1229704397_59137762.html ）：2-6、9-11月 峰 8-11、13-17 平 17-24 谷 0-8、11-13；1、7、8、12月 尖峰 9-11、15-17 峰 8-9、17-23 平 13-15、23-24 谷 0-8、11-13；春节/劳动节/国庆 深谷 10-14（holiday）。**2026-07-01 起 浙发改价格〔2026〕112号**（2026-05-28，废止 21号）https://fzggw.zj.gov.cn/col/col1229123351/art/2026/art_8282044c7b1b469c82f5fd9d2c27a41d.html （95598 副本 `data/raw/zhejiang/tou_112_95598.pdf`）：2-6、9-11月 峰 16-23 平 7-11、14-16、23-24 谷 0-7、11-14；1、7、8、12月 尖峰 18-22 峰 16-18、22-23 平同 谷同；劳动节/国庆节前 3 天及春节假期 0-9 谷、9-15 深谷（holiday）。**142号不是分时文件。**
- **校验**：≤2026-06 对平段价的比率（大工业 尖峰 1.98/峰 1.65，一般工商业 1.65/1.5，谷 0.45，1、7、8、12月 0.38，深谷 0.2）；≥2026-07（112号）2.05/1.85/1/0.4/0.2 × (P+线损+系统运行费) + 输配 + 基金。5 条 `voltage_monotonic` 失败 = 2026-07 两部制 不满1千伏 比 1~10（20）千伏便宜（印出的系统运行费更低），**照原样**。
- **覆盖**：12/12。
- **已知问题**：**DB 中只剩 112号 4 条规则，21号 4 条被河南覆盖（§1.6）**。对账：2026-06..10 差异判为 energydc 错；8 个 `ours_null` 深谷未复核【待核实】。
- **路径**：`pipeline/sources/zhejiang.py`、`data/raw/zhejiang/`。

### 安徽 `anhui`
- **来源**：省发改委月度通告 fzggw.ah.gov.cn 对 curl 403；改用同表的官方文本 PDF 副本：95598.cn omg-static（2025-11..2026-05）+ 芜湖市发改委转载 `https://whfgw.wuhu.gov.cn/fgyw/tzgg/`（2026-06..10，附件需 Referer）。芜湖 2026-05 前只发 JPG，六安只有 1000px PNG。
- **发现**：模块内 `SEED` 字典（无 seed.json）。
- **解析**：pdfplumber。单一制 不满1/1-10/35千伏（2026-08 起加 110/220，**DB 显示 7 → 9 行在 2026-08**；模块 docstring 写"from 2026-06"与 DB 不符，以 DB 为准）+ 两部制 1-10/35/110/220千伏及以上。峰列表头 "1、7-9、12月 高峰" 或 "2-6、10-11月 高峰时段"。尖峰列 1、7、8、12月（单一制 "/"），深谷列两部制在含 3 天以上假期的月份印出（2026-01/02/04/05/06/09/10）。4 位小数（P 5 位）。2026-08 存 2026-07-29 发布的月度表，不存每周修订/实际结算表。
- **分时**：皖发改价格〔2025〕302号（2025-05-30，2025-07-01 起，2026-10 公告仍引用），宿州转载 https://www.ahsz.gov.cn/public/2655575/195697811.html 。7-9月 峰 16-24 谷 2-9、11-13；1、12月 峰 15-23 谷 12-14、23-6；其他月 峰 6-8、16-22 谷 11-14、23-6；其余平。day_type `sharp`：尖峰 7/15–8/31 20-22、12/15–1/31 19-21；day_type `holiday`：3 天以上假期 深谷 11-15（均限 ≥315kVA 两部制工业）。应有 8 条。
- **校验**：`extra_validate` 用 (P+输配) × 302号比率重算峰/谷/尖峰/深谷：峰 +84.3%（1、7-9、12月）/ +74%，谷 −61.8%，尖峰 = 峰 +20%，深谷 = 谷 −20%。
- **覆盖**：12/12。
- **已知问题**：**DB 只剩 3 条 holiday 规则（21006–21008），normal 3 条 + sharp 2 条被山东覆盖（§1.6）**。
- **路径**：`pipeline/sources/anhui.py`、`data/raw/anhui/`。

### 福建 `fujian`
- **来源**：fj.sgcc.com.cn 不可达且未找到自有列表；德化县人民政府转载"国网福建省电力有限公司代理购电工商业用户电价表（M月）"：列表 `http://www.dehua.gov.cn/zwgk/zdxxgk/ggqsy/gd/`，文章 `…/gd/YYYYMM/tYYYYMMDD_NNNNNNN.htm`。
- **发现**：实时：列表页内联 JS 数组（`docreltime/doctitle/docpuburl`），标题只有月份，年份按发布日推断（转载在当月或最多 6 周后）。
- **格式**：HTML `<table>`（`sgcc_cms.html_tables` 展开 rowspan/colspan），存 `data/raw/fujian/YYYY-MM.htm`。
- **解析**：9 行/月（窗口内不变）：不满1、1-10（20）、35、110、220千伏及以上 等（以 DB 为准）。无深谷。
- **分时**：闽发改规〔2023〕8号（2023-11-28，2024-01-01 起，有效 5 年 → effective_to 2028-12-31）https://fgw.fujian.gov.cn/zfxxgkzl/zfxxgkml/bwgfxwj/202311/t20231129_6309872.htm 。峰 10-12、15-20、21-22；谷 0-8；其余平；7-9月 尖峰 11-12、17-18。2 条。（早期曾用 2026-10 公告 URL 作 tou_policy id 与当月 agency_price 冲突，已修。）
- **校验**：浮动仅对 P：峰 +58%，谷 −63%，尖峰 +80%（公告公式行：峰 = 2×(1+58%)+3+4+5+6）。
- **覆盖**：12/12（转载滞后 1–19 天）。
- **路径**：`pipeline/sources/fujian.py`、`data/raw/fujian/`。

### 江西 `jiangxi`
- **来源**：自有列表 `http://www.jx.sgcc.com.cn/html/main/col2786/column_2786_1.html` 对管线和浏览器都不可达；改用 95598.cn omg-static 文本 PDF（浏览器检索定位）+ 寻乌县人民政府转载（2026-04、2026-08）。
- **发现**：`data/raw/jiangxi/seed.json`（`live_list=False`）；2025-11 后补：`LOCAL_FILES = {"2025-11": "95598_2025-11.pdf"}`。
- **解析**：p1 表，`parse_grid(nd=8)`（保留 7 位小数）。P3 单一制 3 行（不满1/1-10/35千伏）+ 两部制 4 = 7；P4 5 + 4 = 9。尖峰（仅 1、7、8、12月）与深谷均照印存（尖峰 "-" → NULL）。parse 校验"执行时间"，拒收错月文件（寻乌"202603"文章实为 2025-09 表）。
- **分时**：赣发改价管〔2025〕463号（2025-06-23；source 为 jx.sgcc 转载 http://www.jx.sgcc.com.cn/html/main/col2786/2025-06/25/20250625152518539619489_1.html ；**463号自身生效日未核实，tou_rule 的 effective_from 为空**【待核实】）。按每月表注2：1、12月 峰 9-12、18-21（尖峰 18-20）谷 0-6；2月 峰 16-22 谷 0-6；7-9月 峰 17-23（7、8月 尖峰 20:30-22:30）谷 1-5、11:30-14:30（深谷 12-14）；3-6、10-11月 峰 16-22 谷 1-5、11:30-14:30（深谷 12-14）；春节/五一/国庆 12-14 深谷。7 条。
- **校验**：只 P 浮动：1.6:1:0.4；尖峰 = 平 + 80%·P；深谷 = 平 − 70%·P。
- **覆盖**：12/12（2025-11 于 2026-10-11 补入）。对账 TOU 12 个月"不同"= energydc 注释解析问题，非我方错。
- **路径**：`pipeline/sources/jiangxi.py`、`data/raw/jiangxi/`。

### 山东 `shandong`
- **来源**：95598.cn P2019（地区 山东），sd.sgcc 不可达。
- **发现**：`data/raw/shandong/seed.json`。
- **格式**：2025-11..2026-07 JPG 海报（2481×5359），2026-08/09 图片 PDF，2026-10 文本 PDF —— 为一致性全部 OCR（PDF 渲染约 3150px；2026-10 OCR 与文本层对比 0 差异）。海报 = 主表（入库）+ 1.5 倍（跳过）+ 电价信息公布表（不入库）。
- **解析**：18 数值列（新能源可持续发展列出现前 17 列）：1 电度 = 2 当月平均购电价格 + 3 容量补偿电价 + 4 历史偏差电费折价（2+3+4 存 `purchase_price`）+ 5 线损 + 6 抽蓄容量 + 7 煤电容量 + 8 新能源差价结算 + 9 各类分摊损益（6..9 之和存 `system_op`）+ 10 基金 + 11 输配；然后尖峰/峰/平/谷/深谷（**每月每行都印**，照存）、需量、容量。P3 7 行：单一制 不满1/1-10(20)/35千伏及以上，两部制 1-10(20)/35-110千伏以下/110-220千伏以下/220千伏及以上；P4（鲁发改价格〔2026〕556号）9 行。
- **分时**：≤2026-09-30：国网山东《关于2026年工商业分时电价公告》（2025-11-28，依 鲁发改价格〔2023〕914号，2026 沿用 2025 时段）https://www.95598.cn/omg-static/99106241082992543400401950838465 ：1-2、12月 谷 2-6、10-15（深谷 11-14）峰 7-9、16-21（尖峰 16-19）；3-5月 谷 10-15（深谷 11-14）峰 17-22（尖峰 17-20）；6月 谷 7-12 峰 16-23（尖峰 17-22）；7-8月 谷 1-6 峰 16-23（尖峰 17-22）；9-11月 谷 10-15（深谷 11-14）峰 16-21（尖峰 17-19）。2026-10-01 起 鲁发改价格函〔2026〕68号（国网山东 2026-09-11 公告）https://www.95598.cn/omg-static/99109111502938089123900953785807 ：3-6月 谷 8-15（深谷 10-14）峰 17-23（尖峰 17-22）；9-11月 谷 9-15（深谷 11-14）峰 16-22（尖峰 17-20）；1-2、12月与 7-8月不变。9 条（5 + 4）。
- **校验**：`_central_util.affine_solve`（共享分量各行相等；电度 = Σ + 输配 = 平；各分时价 − 电度 恒定）。政策浮动：峰 +70%、谷 −70%、尖峰 +100%、深谷 −90%，作用于当月平均购电价格、容量补偿、线损、抽蓄、煤电、新能源差价（历史偏差、分摊、基金、输配不浮动）。`regulated_postprocess`（按日期分期）。
- **覆盖**：12/12；2025-11 目视 0 差异。
- **对账**：89 行差异均为口径（energydc 只存"当月平均购电价格"）或 energydc 列错位；2026-09 energydc 漏单一制 220 并错位两部制。
- **路径**：`pipeline/sources/shandong.py`、`_central_util.py`、`data/raw/shandong/`。

---

## 4.5 华中

### 河南 `henan`
- **来源**：95598.cn P2019（地区 河南）列出"国网河南省电力公司YYYY年M月代理购电价格公告"，但这些 PDF 数字为轮廓字形（文本层是过期的 CorelDRAW 图层），2026-02/04/06/07 是 1149px 位图。平顶山市人民政府（pds.gov.cn › 价格收费 `https://www.pds.gov.cn/channels/38390.html`）转载同一公告的**文本 PDF**（"…公示表-水印版.pdf"，楷体斜向"YYYY年M月D日发布"水印）。
- **发现**：`data/raw/henan/seed.json`（`file_url` = 95598，`text_pdf_url` = 平顶山）。
- **解析**：DB 实际：**2025-11..2026-08 共 10 个月用平顶山文本**（按字体/字号过滤水印字形），**2026-09/10 OCR** 95598 PDF（渲染 5000px，`affine_solve` 交叉校验）。（注：模块 docstring 写文本"2026-01..08"、TOU_NOTES 写 OCR "2025-11、2025-12、2026-09、2026-10"，与 announcements.json 不一致；以 announcements.json/policy_doc 为准：pds 10 个月、95598 2 个月。2025-11/12 的 OCR 与文本逐格一致，仅一个容量格不同，已由周期多数修正。）列：电度、P、线损、输配、基金、系统运行、尖峰、峰、平、谷、需量、容量。P3 8 行（单一制 不满1/1-10/35-110千伏以下/110千伏及以上；两部制 1-10/35-110千伏以下/110/220千伏及以上）；P4 9 行。尖峰仅 1、7、8、12月。
- **分时**：豫发改价管〔2024〕283号（2024-05-29，2024-06-01 起）https://fgw.henan.gov.cn/2024/05-29/3000232.html 。1、2、12月 峰 16-24（1、12月 尖峰 17-19）谷 0-7；3-5、9-11月 峰 16-24 谷 0-6、11-14；6-8月 峰 16-24（7、8月 尖峰 20-23）谷 0-7；其余平。5 条。
- **校验**：283号 1.72:1:0.45，尖峰 = 1.2×峰浮动，作用于 (P+输配)；2026-01 发改委征求意见稿拟取消代理购电用户输配电价浮动，公告间分时价差对输配电价的依赖确有变化 → 用 affine_solve 而非固定系数。
- **覆盖**：12/12；2025-12、2026-09 目视 0 差异。
- **已知问题**：2026-10 两部制 35千伏 高峰曾为 NULL，已修（§2.6）。
- **路径**：`pipeline/sources/henan.py`、`data/raw/henan/`（`YYYY-MM.pdf` 为 95598，`YYYY-MM_pds.pdf` 为平顶山）。

### 湖北 `hubei`
- **来源**：95598.cn P2019（地区 湖北）；hb.sgcc 不可达。
- **发现**：`data/raw/hubei/seed.json`（字段 `file_key`、`page`）。合订本"2025年1-12月"（12 页，**逆序**：p1 = 2025-12，p2 = 2025-11）、"2026年1-6月"（p1 = 2026-06 … p6 = 2026-01），2026-07 起按月。source_url 带 `#page=N`。
- **格式**：图片 PDF（每页一张 2245–3367px 扫描）→ OCR。
- **解析**：12 列：非分时、P、线损、输配、基金、系统运行、尖峰、峰、平（9=1）、谷、需量、容量。**尖峰每月都印**。窗口内 6 位小数（输配 4 位）。P3 7 行；P4 9 行；按 OCR 行数选布局并以输配排序核对。
- **分时**：鄂发改价管〔2024〕77号（2024-03-27，2024-05-01 起）https://fgw.hubei.gov.cn/fbjd/zc/gfwj/gf/202404/t20240401_5141591.shtml ：尖峰 7、8月 20-22，其余月 18-20；峰 7、8月 16-20、22-24，其余 16-18、20-24；平 6-12、14-16；谷 0-6、12-14。节假日深谷 鄂发改价管〔2025〕86号（2025-05-01 起）https://fgw.hubei.gov.cn/fbjd/zc/gfwj/gf/202504/t20250411_5612469.shtml ：春节/劳动节/国庆 9-15 深谷（holiday，月份 1、2、5、10）；表内不印深谷价 → NULL。3 条。
- **校验**：浮动作用于 P+线损：1.8:1.49:1:0.48（7、8、12、1月 尖峰 2、谷 0.45）→ 各行价差相同 → `affine_solve`。`regulated_postprocess`（**按行数分期**）。
- **覆盖**：12/12；2025-12、2026-07、2026-10 目视 0 差异。110千伏 需量/容量为跨行合并格（42/26.3 跨 1-10&35；39/24.4 跨 110&220），我方正确。
- **路径**：`pipeline/sources/hubei.py`、`data/raw/hubei/`。

### 湖南 `hunan`
- **来源**：95598.cn P2019（地区 湖南），标题"YYYY年M月代理购电电价"。
- **发现**：`data/raw/hunan/seed.json`。
- **格式**：PNG 海报（3023px，无水印）：表1 主表（入库）+ 1.5 倍/价格信息/分时平均电价表（不入库）。
- **解析**：11 列：平时段电度电价（= 电度，同时存 energy_price 与 flat）、上网电价（P）、线损、输配、系统运行、基金、尖峰/峰/谷、需量、容量。**行序高→低电压**：两部制 220/110/35/1~10，然后单一制；P3 单一制 110及以上/35/1~10/不满1（8 行），P4 220/110/35/1~10/不满1（9 行）。尖峰仅尖峰月印出。
- **分时**：湘发改价调规〔2025〕385号（2025-06-18 签发，2025-08-01 起 5 年，effective_to 2030-07-31；替代〔2021〕848号）http://fgw.hunan.gov.cn/fgw/xxgk_70899/zcfg/dfxfg/202507/t20250702_33727950.html 。每月 谷 0-6、12-14，平 6-12、14-16，峰 16-24；尖峰 7、8月 20-24，1、12月 18-22。3 条。
- **校验**：1.6:1:0.4 作用于 (P+线损+输配)；尖峰 = 峰 ×1.2；`affine_solve`；`regulated_postprocess`（P3/P4 按日期）。
- **覆盖**：12/12；2026-10 目视 0 差异。
- **路径**：`pipeline/sources/hunan.py`、`data/raw/hunan/`。

---

## 4.6 西南

### 重庆 `chongqing`
- **来源**：`http://www.cq.sgcc.com.cn/html/main/col2680/column_2680_1.html`（别名 cq.sgcc.cn），标题 `代理购电价格公示表-YYYY年M月(发布)`。
- **发现**：实时（`CMSRegion`，max_pages 3，alt_hosts cq.sgcc.cn）。每篇文章是图集（2 张 1785×1262 JPG，通常同图，按 sha256 去重）。
- **ALT_PDF**（国网 95598 文本 PDF 替代）：2025-11（图集 JPG `/html/files/2025-10/28/20251028181553653465677.jpg` 两主机均 404）→ https://www.95598.cn/omg-static/99307090136555352674600002747812 ；2026-03（1262×892 低清重渲染，OCR 不可靠）→ https://www.95598.cn/omg-static/99307090116759420310901934550254 ；2026-05 → https://www.95598.cn/omg-static//omg-static/99304271882705328964600637163120.pdf 。本地文件 `ALT_LOCAL`：`95598_2025-11.pdf`、`95598_2026-03.pdf`、`2026-05_95598.pdf`。2026-01 OCR 已与其 95598 PDF 逐格比对 0 差异（`data/raw/chongqing/xcheck_2026-01_95598.pdf`）。
- **解析**：OCR（eng、数字白名单、×3 放大，OCR_VERSION 3，缓存 `ocr_cache/`，键 sha256+版本）；文本月用 `cms.parse_grid`。行标签来自 `LAYOUTS`（按行数）：P3 8 行（单一制 不满1/1-10/35/110千伏及以上 + 两部制 1-10/35/110/220及以上）；P4 9 行（单一制 5 + 两部制 4）。尖峰 1、7、8、12月；无深谷；不满1千伏 行不印分时价。
- **分时**：渝发改规范〔2021〕14号（2021-12-09，2022-01-01 起，窗口内公告仍引用）https://fzggw.cq.gov.cn/zwgk/zfxxgkml/zcwj/xzgfxwj/sfzggwxzgfxwj/202112/t20211209_10119678.html 。峰 11-17、20-22；平 8-11、17-20、22-24；谷 0-8；尖峰 12-14 于 1、7、8、12月。2 条。
- **校验**：平 = 非分时；峰 = 平 + 60%·B，谷 = 平 − 62%·B，尖峰 = 峰 + 20%·1.6·B，B = P + 输配（基金/线损/系统运行费不浮动）；输配随电压递减。`postprocess` = `regulated_consensus`（**默认按行数分期，会改到文本月**，见 §2.4）+ 2026-02 守卫式修复。
- **覆盖**：12/12（99 行）。README 中 "10/12、83 行" 已过时（2026-10-11 后补 2025-11、2026-03）。
- **已知问题**：2026-02 线损/基金互换已修（§2.6）。修复记录：2026-04 单一制 不满1千伏 输配 0.2821→0.2321；2026-07/08 两部制 35千伏 需量 36.2/38.2→35.2（2026-08 图像核实）。
- **路径**：`pipeline/sources/chongqing.py`、`data/raw/chongqing/`。

### 四川 `sichuan`
- **来源**：`http://www.sc.sgcc.com.cn/html/main/col2804/column_2804_1.html` 对管线与浏览器均空回复；走 95598.cn P2019（地区 四川）。
- **发现**：`data/raw/sichuan/seed.json`（`live_list=False`）；`LOCAL_FILES = {"2026-06": "95598_2026-06.pdf"}`（95598"6月"条目挂的是 7 月文件，6 月表另找到独立 omg-static PDF）。
- **解析**：表1 四川省省级代理购电工商业用户电价表（p1，入库），表2 1.5 倍、表3 公开信息表跳过。列：非分时 | P、线损、输配、基金、系统运行 | 尖峰、峰、平、谷 | 需量、容量。尖峰在表印出的月份为两部制行（大工业尖峰）照存；无深谷。P3 单一制 3 行（不满1/1-10/35千伏及以上）+ 两部制 4 = 7；P4 单一制 5 → 9。
- **分时**：川发改价格〔2025〕185号（2025-04-30，2025-05-01 起）https://fgw.sc.gov.cn/sfgw/qtwj/2025/4/30/8f5b20c4de7e4df48faa04935e512111.shtml ；川发改价格〔2025〕649号（2026-01-01 起）对代理购电用户沿用。春秋(3-6、10、11月) 峰 10-12、17-22 谷 22-8；夏(7-9月) 峰 11-18、20-23 谷 1-7；冬(12、1、2月) 峰 10-12、16-22 谷 0-8；其余平。尖峰（大工业/两部制）7-8月全月 13-14、21-23，其他月仅成都预报连续 3 天 ≥35℃ 时（未建规则）。4 条。
- **校验**：峰/谷 = 平 ± 60%·(P+线损+输配)；尖峰 = 峰 +20%。
- **覆盖**：**11/12，缺 2026-09**：95598 条目（releaseInfoId 99ef361f49c8444ea2123fa1853ba744）只有 .docx 附件，无预览/静态 URL（JS blob 下载）。
- **待办**：用浏览器在 95598 详情页尝试下载 docx 并存盘（不涉及登录）→ 加 `LOCAL_FILES["2026-09"]` 与 docx 解析；或找政府转载（如乐山 leshan.gov.cn，沙箱可达）【待核实】。
- **路径**：`pipeline/sources/sichuan.py`、`data/raw/sichuan/`。

### 西藏 `xizang`
- **来源**：95598.cn P2019 西藏列表只有两份文件：藏政办发〔2023〕28号（2023-11-14 印发，2023-12-01 起，95598 发布 2024-05-23，文本 PDF）与 藏发改价格〔2026〕254号（2026-09-03，第四监管周期输配电价，宣布 2026-10-01 起组织发电侧与售电侧市场交易；扫描件，未入库）。**无月度代理购电序列**，工商业仍执行目录销售电价。
- **入库**：tariff_month 为空；tou_rule 2 条 = 28号 大工业 分季节峰谷：丰水期 5-10月 平 0-3、6-11、17-24，谷 11-17，深谷 3-6；枯水期 1-4、11-12月 平 0-9、13-19，峰 9-13、22-24，尖峰 19-22。source https://www.95598.cn/omg-static/99308100976112223958902124993974 。目录电价（大工业 10/35/110千伏及以上 平段 0.67/0.66/0.65 元/kWh 等）只写在 JSON notes。
- **待办**：2026-10 起市场化交易，**代理购电可能开始发布** → 每月复查 95598 西藏列表；若出现，按西北组模板新建解析。
- **路径**：`pipeline/sources/xizang.py`。

---

## 4.7 西北

（西北组共用 `_northwest_util.py`：seed 发现、魔数校验下载、加盐 policy_doc id、pdfplumber 选页、`fill_shared`、通用划线表 OCR。全部省站不可达，数据均来自 95598.cn omg-static；浏览器笔记 `/workspace/web/notes/55dbe641-…/p2019-*/`。）

### 陕西（不含榆林）`shaanxi` / 陕西榆林 `shaanxi-yulin`
- **来源**：95598.cn P2019（地区 陕西）；sn.sgcc 不可达。**同一份月度 PDF** 含两张表：p1 代理购电工商业用户电价表（不含榆林地区）、榆林地区表（p2 或 p3）；2026-05 起多出 1.5 倍页（跳过）；末页价格表。
- **发现**：`data/raw/shaanxi/seed.json`、`data/raw/shaanxi-yulin/seed.json`（URL 相同）。
- **解析**：pdfplumber → `parse_grid`；P/线损/系统运行/基金为整表合并格，pdfplumber 只放在某一行（2026-10 在 220千伏行）→ `fill_shared`。选页：shaanxi `PAGE_WANT = [代理购电工商业用户电价表, 不含榆林]`；yulin `[代理购电工商业用户电价表, （榆林地区）]`。category = 表内用电分类去掉（单一制）/（两部制）：`一般工商业用电`（单一制）/ `大工业用电`（两部制）。P3 8 行（单一制 不满1/1-10(20)/35/110 + 两部制 1-10(20)/35/110/220(330)）；P4 单一制加 220千伏及以上 → 9 行。尖峰列非尖峰月印 0 → NULL（`zero_to_none`）。榆林只有两部制输配电价不同。yulin 的 policy_doc id 加盐 `#榆林` 防止与 shaanxi 冲突。
- **分时**：陕发改价格〔2025〕1034号（2025-07-21 签，2025-07-23 发布，2025-08-01 起；替代〔2021〕1757号）https://sndrc.shaanxi.gov.cn/zfxxgk/zc/fgwj/sfzggwwj/2025/202507/t20250723_3546317.html 。峰 16-23；谷 0-6、11-14；平 6-11、14-16、23-24；尖峰 7、8月 19-21，1、12月 18-20。各 3 条。
- **校验**：只 P 浮动：峰/谷 = 平 ± 70%·P，尖峰 = 平 + 90%·P（精确）。
- **覆盖**：均 12/12、99 行；对账修正 caption 映射后 0 差异（剩余 66 个 `ours_null` 尖峰是 energydc 把 "—" 写成 0.0）。
- **路径**：`pipeline/sources/shaanxi.py`、`shaanxi_yulin.py`、`data/raw/shaanxi*/`。

### 甘肃 `gansu`
- **来源**：95598.cn P2019（地区 甘肃）；gs.sgcc 无可用列表。
- **发现**：`data/raw/gansu/seed.json`。**95598 甘肃列表从 2026-01 开始，无 2025-11、2025-12**（搜"代理购电"/"11月"/"12月"并翻完 4 页）。
- **格式**：PNG 海报（2026-01/02/03、08/09/10，2245px）、图片 PDF（04、05、07，p1 一张图）、文本 PDF（06）。
- **解析**：市场化分时表：14 列 = P 峰/平/谷、线损 峰/平/谷、输配、系统运行、基金、分时电度 峰/平/谷、需量、容量（10=1+4+7+8+9、11=2+5+7+8+9、12=3+6+7+8+9）。无非分时价 → `energy_price` = 平时段总价（列11），`purchase_price`/`line_loss` = 平时段值；峰/谷时段值存 `announcements.json` 的 `period_prices`。无尖峰/深谷。行以输配电价认行：P3 7 行（输配 0.2965/0.2765/0.2565/0.1028/0.0888/0.0764/0.0658，发改价格〔2023〕526号）；P4 9 行（0.2945/0.2745/0.2545/0.1251/0.1134/0.1063/0.0923/0.0799/0.0693）。
- **分时**：甘发改价格〔2024〕424号（2024-07-05 签，2024-08-01 起），官方扫描 https://www.95598.cn/omg-static/99307021080468606529101523659698 。全年一条：峰 6-8、18-23；平 23-6、8-10、16-18；谷 10-16。
- **校验**：OCR 仅当每行三个等式**精确**成立且输配与周期列表逐行相等才接受。`postprocess` = `regulated_consensus`（行数分期）。
- **覆盖**：**10/12**（缺 2025-11、2025-12，95598 未发布，未找到其他官方免费副本）；2026-01、2026-10 目视 0 差异。
- **对账口径**：energydc 取的是高峰线损列（0.006517）而我方为平段（0.005490），70 格差异全部为口径；energydc TOU 解析漏平段。
- **路径**：`pipeline/sources/gansu.py`、`data/raw/gansu/`。

### 青海 `qinghai`
- **来源**：95598.cn P2019（地区 青海）；qh.sgcc、fgw.qinghai.gov.cn 不可达。2025-11..2026-07 于 2026-07-10 在 95598 统一（重新）挂出，PDF 首页保留原签发日（如 2025-10-28）。
- **发现**：`data/raw/qinghai/seed.json`。
- **解析**：文本 PDF：p1 公告、p2 附件1 主表（+注）、末页附件2 信息表；pdfplumber → `parse_grid`，合并格填充。P3 7 行：单一制 不满1/1-10/35 + 两部制 1-10/35/110-220/330千伏；P4（青发改价格〔2026〕405号）9 行（单一制加 110/220及以上，两部制顶行变 220千伏及以上）。无尖峰/深谷列。
- **分时**：时段按每月公告注2：峰 7-9、17-23；谷 9-17；其余平（全年）。政策：青海省发改委《关于优化完善我省峰谷分时电价政策的通知》（2024-04-09，2024-05-01 起），**文号未获取**（发改委网站不可达）→ `doc_no` 为空，source 用 2026-10 公告 95598 URL https://www.95598.cn/omg-static/99309241946254462759200214872640 。该政策转载文本另有尖峰（一、四季度 8-9、19-21，平 +100%），但窗口内公告既未提及也未定价 → 不存。1 条。
- **校验**：峰 = 平 + 63%·P，谷 = 平 − 65%·P（精确）。
- **覆盖**：12/12。
- **待办**：补文号【待核实】。
- **路径**：`pipeline/sources/qinghai.py`、`data/raw/qinghai/`。

### 宁夏 `ningxia`
- **来源**：nx.sgcc.com.cn 信息公开 › 供电企业执行的电价和收费标准（col502367），文章"代理购电价格表（YYYY年M月）"一个 PDF；沙箱 curl 超时，**浏览器可访问**并把 PDF 存入 `data/raw/ningxia/YYYY-MM.pdf`（download 跳过已存在文件）。文章无发布日期 → publish_date 取 URL 路径日期。
- **发现**：`data/raw/ningxia/seed.json`（`live_list=False`）。
- **解析**：图片 PDF（2048px 扫描：p1 主表、p2 信息表）→ OCR（`find_tables/read_table`，表框贴边需把 x0 夹到 3），先 OCR 表头带确认固定列序。市场化分时：P 按时段（峰/平/谷 交易加权均价，列 1-3），分时电度 = 时段 P + 线损 + 输配 + 基金 + 系统运行。无非分时价 → `energy_price` = 平时段总价，`purchase_price` = 平时段 P；峰/谷 P 存 `announcements.json` 的 `period_purchase_prices`。单一制需量/容量印 0 → NULL。P3 7 行（单一制 不满1/1-10/35-110 + 两部制 1~10/35/110/220及以上），输配 0.1846/0.1646/0.1446/0.0920/0.0769/0.0600/0.0521；P4 9 行。
- **分时**：≤2026-03-31：峰 7-9、17-23，谷 9-17，平 0-7、23-24（宁发改运行〔2024〕952号，按公告注6转述；source 为国网宁夏 2025-10-28 PDF）。2026-04-01 起：峰 21-次日5，谷 9-17，平 5-9、17-21（《宁夏回族自治区电网企业代理购电工作实施细则》宁发改价格（管理）〔2026〕79号，https://fzggw.nx.gov.cn/tzgg/202602/t20260212_5170559.html ）。2 条。
- **校验**：公告公式行 + 监管输配电价。**21 条 `tou_order` 失败 = 2026-01..03 峰价低于平价（市场结果），真实**。
- **覆盖**：12/12。
- **路径**：`pipeline/sources/ningxia.py`、`data/raw/ningxia/`。

### 新疆 `xinjiang`
- **来源**：95598.cn P2019（地区 新疆）；xj.sgcc 不可达。
- **发现**：`data/raw/xinjiang/seed.json`。
- **格式**：每月一张海报（JPG；2025-12 PNG），宽 769–9450px，统一重采样到 2270px；表1 主表（入库）、表2 1.5 倍（跳过）、表3 信息表。
- **解析**：市场化分时：代理购电价格为多行合并格"[均价] 其中：[尖峰 x] 高峰 x 平时段 x 低谷 x [深谷 x]"；分时电度 X = 时段 P + 线损 + 输配 + 基金 + 系统运行；非分时 = 平时段。存：`energy_price` = 非分时（= 平），`purchase_price` = 平时段 P（= 电度 − 线损 − 输配 − 基金 − 系统运行），时段 P 存 `period_purchase_prices`。尖峰 1、7、11、12月，深谷 5-8月，照印存。P3 7 行（输配 0.1636/0.1606/0.1566/0.1204/0.1100/0.0815/0.0486，新发改能价〔2023〕243号）；P4 9 行。基金 0.0041（注1：0.0021 + 0.002）。
- **分时**：新发改规〔2023〕11号（2023-08-30，2023-10-01 起）https://xjdrc.xinjiang.gov.cn/xjfgw/c108361/202312/e5c0f50c7028486ebe36ea253829b340.shtml 。峰 8-11、19-24；平 11-13、17-19、0-4；谷 4-8、13-17；尖峰 7月 21-23、1、11、12月 19-21；深谷 5-8月 14-16。4 条。
- **校验（接受条件）**：每行 1=9；输配逐行等于周期列表；各分时列减去(线损+输配+基金+系统运行)在所有行得到同一时段 P；这些时段 P 必须都出现在单独 OCR 的合并格中。2025-11（967px）、2025-12（769px）低清：在原像素上 12 种变体投票的候选求解器。`postprocess` = `regulated_consensus`（行数分期）。
- **覆盖**：12/12；2025-11、2025-12、2026-02、2026-10 目视 0 差异。
- **对账口径**：energydc 存"均价"，我方存平时段 P（2025-11/12 差异为口径）。
- **路径**：`pipeline/sources/xinjiang.py`、`data/raw/xinjiang/`。

---

## 4.8 南方电网

### 广东 5 价区：`guangdong-prd5` / `guangdong-jiangmen` / `guangdong-huizhou` / `guangdong-eastwest` / `guangdong-north`
- **来源**：95598.csg.cn API，areaCode `030000`，标题"广东电网有限责任公司关于YYYY年M月代理购电工商业用户价格的公告"；`link` 为静态页，内含 downloadFiles PDF（+JPG 预览）。
- **发现/下载**：`_guangdong_util.discover_notices`；PDF 只下一次到 `data/raw/_guangdong_notices/gd/YYYY-MM.pdf`，再复制到各价区目录。
- **格式**：文本 PDF，每个价区一页一表；单位**分/kWh**（÷100），6 位小数；需量/容量单位元。
- **价区映射**（`GDArea` 按月份匹配表标题 `代理购电(工商业)用户电价表（<价区>）`）：
  - prd5：`珠三角五市`（广州、珠海、佛山、中山、东莞）≤2026-07；`珠三角6市`/`珠三角六市` ≥2026-08（= 五市 + 江门 不含恩平/台山/开平；粤发改价格〔2026〕242号 价区调整）。
  - jiangmen：`江门市` ≤2026-07；≥2026-08 用 `珠三角6市` 表（**与 prd5 数值相同**，用户待决，见 §6）。
  - huizhou：`惠州市`；eastwest：`东西两翼地区`（汕头、潮州、揭阳、汕尾、阳江、湛江、茂名、肇庆 + 江门恩平/台山/开平）；north：`粤北山区`（云浮、河源、梅州、韶关、清远 不含连州）。
- **解析**：6 行/月 → 2026-08 起 7 行（单一制加 220千伏及以上）。尖峰**每月都印**（高温日适用）→ 照存；无深谷。1.5 倍规则只在注7 文字中。
- **分时**：粤发改价格〔2021〕331号（2021-08-31，2021-10-01 起，窗口内每月公告仍引用），江门转载 http://www.jiangmen.gov.cn/jmfgj/gkmlpt/content/2/2405/post_2405749.html （扫描副本 `web/downloads/guangdong_tou_331.pdf`，已 OCR 核对）。全省 峰 10-12、14-19，谷 0-8，其余平，1.7:1:0.38；尖峰 11-12、15-17 于 7、8、9月全月 + 其他月份广州预报最高温 ≥35℃ 的日子（day_type `high_temp`），尖峰 = 峰 +25%。各 3 条（ids 31001+/31101+/31201+/31301+/31401+）。
- **校验**（`formula_check`，精确）：对每个分量 c ∈ {P, 线损, 输配, 系统运行} 分别四舍五入到 0.01 分：峰 = 基金 + Σ r(1.7c)，谷 = 基金 + Σ r(0.38c)，尖峰 = 基金 + Σ r(1.25·r(1.7c))，平 = 非分时 —— 0 不符。eastwest 与汕头政府镜像 11 个月 0 差异。
- **覆盖**：均 12/12、75 行。
- **路径**：`pipeline/sources/_guangdong_util.py`、`guangdong_*.py`、`data/raw/guangdong-*/`。

### 深圳 `shenzhen`
- **来源**：95598.csg.cn API areaCode `090000`，标题"深圳供电局有限公司YYYY年M月代理购电价格"，`link` 即 PDF。存 `data/raw/_guangdong_notices/sz/`。
- **解析**：只取**表1-1 深圳市**（不含深汕、前海供电、招商供电）。category = 用电类别（一、工商业用电(101至3000kVA) / 二、工商业用电(3001kVA及以上) / 三、工商业用电(100千伏安及以下和公变接入用电)）+ 一/二类的月用电量档（`250kW·h及以下/千伏安·月`、`250kW·h以上/千伏安·月`、`400kW·h及以下/千瓦·月`、`400kW·h以上/千瓦·月`，高档便宜 2 分），以 ` | ` 连接。一、二类为两部制（需量、容量每类印一次，合并格复制到各行），三类为单一制。voltage 原文：10千伏高供低计(380V/220V计量) / 10千伏高供高计 / 20千伏 / 110千伏 / 220千伏及以上；三类 ≤2026-07 只有一行 `不分电压等级`（rank NULL），≥2026-08 有 5 个电压行（21 → 25 行/月）。**无线损列**（注2：输配电价含线损）→ `line_loss = 0.0`。
- **分时**：深发改〔2021〕1005号（2021-12-28，2022-02-01 起，经 粤发改价格函〔2021〕2490号）https://fgw.sz.gov.cn/zwgk/qt/tzgg/content/post_9493596.html 。时段同广东；比价按类别/电压（1005号附件1），非单一比率。3 条（31501+）。
- **覆盖**：12/12、264 行。
- **对账**：energydc 表结构不同，145/264 行可匹配；差异经核为 energydc 问题。
- **路径**：`pipeline/sources/shenzhen.py`。

### 深汕特别合作区 `shenzhen-shenshan`
- 同一深圳公告的**表1-2**（广东式表格），分时按其注6 适用 粤发改价格〔2021〕331号（非深圳 1005号）。6 → 7 行。12/12、75 行、3 条（31601+）。`pipeline/sources/shenzhen_shenshan.py`。

### 广西 `guangxi`
- **来源**：95598.csg.cn API areaCode `040000`，标题 `YYYY年M月代理购电相关价格表`（挂 4 个栏目，附件字节相同）；`link` 为静态页（3 张 JPG 预览 + downloadFiles PDF）。
- **解析**：PDF p1 主表（入库）、p2 1.5 倍（跳过）、p3 代理购电信息表。列：电量电价 | P | 线损 | 输配 | 系统运行 | [其他（仅第三周期）] | 基金 | 峰 | 平 | 谷 | 需量 | 容量。P3 7 行；P4（发改价格〔2026〕1077号）9 行。无尖峰/深谷。
- **"其他"列**：≤2026-07 表多一列"其他"（= 信息表"其他折价"，峰谷分时电价损益分摊，所有行同值，如 2025-11 为 0.008410），schema 无此字段 → **未存**，导致 2025-11（+0.00841）、2025-12（+0.00561）、2026-02（+0.001501）的 21 条 `components_sum` 失败；其他月份差值在容差内。2026-08 起该项并入系统运行费（信息表第 15 项）。值记在 `announcements.json` 的 `other_component_not_stored`。**用户待决**（§6）。
- **分时**：**不存 tou_rule**。公告只写"峰谷分时电价按自治区发展改革委现行有关规定执行"；最后可核实官方文本 桂发改价格〔2023〕609号（峰 11-13、17-23；平 7-11、13-17；谷 23-7）已被替代；2024-11 征求意见稿（峰 16-24；平 0-1、7-12、14-16；谷 1-7、12-14）似已定稿，但官方正文无法获取（fgw.gxzf.gov.cn 不可达、无存档；浏览器笔记 `/workspace/web/notes/55dbe641-…/gxfgw-tu_6sef4v2t7emt3bz3q2tfot3jwg/`）。
- **校验**：峰/谷 = 平 ± D，D = 50% × 当月平均上网电价（信息表第 5 项），每月核对。
- **覆盖**：12/12。
- **路径**：`pipeline/sources/guangxi.py`、`_csg2_util.py`。

### 贵州 `guizhou`
- **来源**：95598.csg.cn API areaCode `060000`，标题"贵州电网有限责任公司YYYY年M月代理购电用户电价公告"（3 个栏目同 PDF）。
- **解析**：PDF 公告正文 pp1-2、p3 代理购电工商业用户电价公告表（入库）、1.5 倍（跳过）、信息公开表。列：电量电价 | P | 电量输配电价(不含线损) | 线损 | 系统运行 | 基金 | 峰 | 平 | 谷 | 需量 | 容量；P/线损/系统运行/基金合并格前向填充。单一制行不印分时价（481号仅两部制执行）→ NULL。P3 7 行；P4（黔发改价格〔2026〕421号）9 行。
- **分时**：黔发改价格〔2023〕481号（2023-06-28，2023-08-01 起），95598.csg 官方扫描 https://95598.csg.cn/ucs/sr/info/06/a8d3146e14ed41d1ae18c8a7547c3cdc/20260730105307/60836cab0a254f06b07f214e1db42486.html ：峰 10-13、17-22；平 8-10、13-17、22-24；谷 0-8；存为 2023-08-01..2026-01-31 与 2026-03-01 起两段。**2026-02 例外**：当月公告注2 印 峰 10-11、17-22；平 8-10、11-13、15-17、22-24；谷 0-8、13-15 → 单独一条（2026-02-01..02-28，source = 该公告静态页 …/20260130093930/498d44adaf634369b0cdf3bfab4e8fa1.html，无文号）。3 条。
- **校验**：峰/谷 = 平 ± 60%·B；B = P+输配+线损+系统运行（≤2026-01），B = P+线损（≥2026-02，黔发改价格〔2026〕47号；2026-03-01 起直接交易用户不再执行目录分时）。
- **覆盖**：12/12。`data/raw/guizhou/seed.json` 与 `*.md`（早期县政府网页抓取）**已不再使用**。
- **路径**：`pipeline/sources/guizhou.py`。

### 海南 `hainan`
- **来源**：95598.csg.cn API areaCode `070000`，标题"海南电网有限责任公司代理购电工商业用户电价表（YYYY年M月）"，`link` 即 PDF。
- **解析**：p1 主表（含注1-4，含分时），p2 代理购电价格表。列：电度 | P | 输配 | 系统运行 | 线损 | 基金 | [尖峰（5、6、7月）] | 峰 | 平 | 谷 | 需量 | 容量。category 承载分类：`工商业用电(100千伏安以下)` = 单一制（含行政事业单位办公），`工商业用电(100千伏安及以上)` = 两部制。P3：单一制 2（不满1/1-10）+ 两部制 4（35千伏以下 rank 2 / 35 / 110 / 220及以上，需量 35.2 / 容量 22）= 6；P4（发改价格〔2026〕1077号）单一制 5 + 两部制 4 = 9。尖峰仅 5-7月表印出。
- **分时**：琼发改规〔2021〕18号（2021-12-21，2022-02-01 起），95598.csg 官方页（正文图片 + 附件1 xls）https://95598.csg.cn/ucs/sr/info/hn/07-firm/211223112311/52b72e0f866e4826a0508bee193c8298.html ：峰 10-12、16-22；平 7-10、12-16、22-23；谷 23-7；尖峰 5、6、7月 20-22（峰 +20%）。2 条。充换电设施另有时段（琼发改规〔2024〕2号）不存。
- **校验**：峰 = 平 + 70%·B，谷 = 平 − 60%·B，尖峰 = 峰 + 20%·1.7B；B = P+输配（≤2026-07）、B = P（≥2026-08）。
- **覆盖**：12/12、81 行。
- **路径**：`pipeline/sources/hainan.py`。

### 云南 `yunnan`
- **来源**：95598.csg.cn API areaCode `050000`，标题"云南电网有限责任公司YYYY年M月代理购电工商业用户电价表"。**API 只有 2026-06..10**。`link` 为 PDF（2026-06、08）或静态页（2026-10 内含 PDF 链接；2026-07、09 只有一张长 PNG → `YYYY-MM.png`）。
- **解析**：PDF 内表格是图片（p1），注释在 pp4-6 为文本。14 划线列：用电分类 | 电压 | 用户电价 | P（合并）| 线损 | 输配 | 系统运行费（"-"）| 基金 | 尖峰（"-"）| 峰 | 平 | 谷 | 需量 | 容量。行：两部制 不满1/1-10/35/110/220(及以上)，然后单一制 不满1/1-10/35（P3，8 行）+ 110/220及以上（P4，云发改价格〔2026〕602号，10 行）。OCR（OCR_VERSION 4）：整表一次 tesseract（数字，psm 11）按位置归格 + 逐格重读；常丢前导"0." → 纯数字串按 int(s)/10^len(s) 解读；位数不对的读数拒收（tesseract 会丢窄的 '1'）。系统运行费 "-" → NULL（结算后在下期账单体现，注9-11），尖峰 "-"（暂缓）→ NULL。
- **分时**：云发改价格〔2024〕924号（2024-11-18，施甸县转载 https://shidian.gov.cn/info/11678/3666103.htm ，2025-01-01..2026-02-28）与 云发改价格〔2026〕51号（2026-01-19，2026-03-01 起，95598.csg 官方 PDF，见 §7.3），时段相同：峰 7-9、18-24；平 0-2、6-7、9-12、16-18；谷 2-6、12-16；1.5:1:0.5 仅对 P；尖峰暂缓。2026-03-01 起目录分时只适用代理购电大工业与 ≥100kVA 一般工商业，<100kVA 单一制执行平段价（注6）。2 条。
- **校验**：用户电价 = P+线损+输配+基金 = 平；峰/谷 = 平 ± 50%·P（精确到 1e-6）；合并格 P 不一致时用 2·(峰−平)。需量/容量逐格 3 变体投票 + 月内按电压多数。**46 条 `components_missing`（系统运行费 NULL）为预期**。
- **覆盖**：**5/12（2026-06..10）**；缺 2025-11..2026-05：API 未发布、yn.csg.cn 不可达、楚雄州发改委转载列表 403。5 个月全部目视 0 差异。
- **路径**：`pipeline/sources/yunnan.py`、`data/raw/yunnan/`。

---

# 5. 对账 SOP（energydc.cn，仅交叉核对）

## 5.1 原则

- energydc.cn 是第三方解析站；其 `/policy` 公开页对 2025-11..2026-10 的 33 个区域 slug 几乎每月都有完整表格，但 **ToS §4 禁止复用**，且其抽取本身有错误（合并单元格扩散、列错位、OCR 错位、TOU 注释解析丢段）。
- 只用来**指出可疑格子**；任何结论都必须回到我们自己的官方原件（`data/raw/<region>/`）核对后才算数；修复值取自官方原件。
- `compare_energydc.py` 对 `data/tariff.db` **只读**，输出只写 `work/compare/`；原始响应缓存 `data/raw/energydc/policy_<uuid>.json`（约 200 个），sitemap 缓存 `data/raw/energydc/sitemap_policy_1.xml`。
- 抓取约束：仅访客公开接口（`https://energydc.cn/edc/api/v1/eprice/policy/<uuid>`，即公开 /policy 页自身发出的 XHR），请求间隔 ≥2 s，≤2 次重试，经 `pipeline/sources/energydc_api_probe.py`；**不调用 /query 历史（会员门控）、不下载付费 CSV/扫描包、不登录**。

## 5.2 用法

```bash
python -m pipeline.compare_energydc --region jiangsu --region shanghai     # 逐区域，缺的 energydc 页会抓取
python -m pipeline.compare_energydc --all-loaded                           # DB 中所有区域
python -m pipeline.compare_energydc --region shanxi --no-fetch             # 只用缓存
python -m pipeline.compare_energydc --region shanxi --budget 110           # 每区域抓取时间上限（秒）
python -m pipeline.compare_energydc --rebuild-summary                      # 只重建 summary.csv + xlsx
python -m pipeline.compare_energydc --region x --months 2025-11..2026-10   # 默认就是该区间
```

区域映射 `REGION_MAP`（我方代码 → energydc slug + 子表标题正则）：如 `jibei→hebei-jibei`、`mengxi→mengxi-grid`、`mengdong→mengdong-grid`、广东 5 价区都映射到 `guangdong` 并按标题选表（`珠三角(五|5)市` / `珠三角(6|六)市`、`惠州`、`江门`、`东西两翼`、`粤北`）、`shenzhen` 用 `^(?!.*深汕)`、`shenzhen-shenshan` 用 `深汕`、`shaanxi` 用 `不含榆林`、`shaanxi-yulin` 用 `(?<!不含)榆林`。排除表：`1\.5|倍|转供|充换电|充电|电动汽车`。同月多页（安徽 2026-07/08 有周版/实际结算、山东 2026-10 ×3、浙江 2026-07 ×3）按标题规则取月度主表。

## 5.3 判定方法

1. 行匹配：按 (月, 计价方式, 规范化电压, 规范化分类) 配对（含 `千瓦安→千伏安` 等规范化）。
2. 逐字段比较：电价容差 0.00005 元/kWh，需量/容量容差 0.05；分类为 `value_diff`、`ours_null`（我方 NULL、对方有值）、`edc_null`、未匹配行。
3. TOU：把 energydc 注释文本解析为时段，与我方 tou_rule 比较：`same` / `same_except_sharp(edc text parse has no 尖峰)` / `different`。
4. **人工复核**：对 top 差异逐格对照官方原件（必要时渲染图像，证据存 `work/compare/evidence/`），结论写入 `work/compare/review_verdicts.json`（区域 → 文字判词），`--rebuild-summary` 时并入 `summary.csv` 的 `review` 列；确认为我方错误的写 `work/compare/our_errors.md`，由区域模块修复。
5. 输出：`work/compare/<region>.csv`（差异/未匹配/覆盖/TOU 明细）、`<region>.json`（汇总行）、`summary.csv`、`compare_report.xlsx`（汇总、差异明细、TOU）；进度追加到 `work/progress_compare.md`。

## 5.4 结论摘要（2026-10-11，两轮复核）

- **确认的我方错误（均已修）**：① 重庆 2026-02 线损/基金 OCR 互换（8 行）；② 河南 2026-10 两部制 35千伏 高峰 NULL。
- **口径差异（非错误）**：
  - **山东购电价**：energydc 只存"当月平均购电价格"；我方 `purchase_price` = 当月平均 + 容量补偿 + 历史偏差（例 2026-05：0.2993+0.0705−0.0160 = 0.3538）；`system_op` = 4 子列之和。
  - **新疆**：energydc 存合并格里的"均价"；我方存平时段 P（使 非分时 = 平 = Σ分量）。
  - **甘肃线损/平段**：energydc 取高峰线损列（0.006517），我方为平段线损（0.005490），与 energy_price = 平时段总价一致；energydc TOU 解析漏了平段。
  - 北京 2026-10 单一制 1-10 峰：官方 0.99322375，我方 6 位取整 0.993224。
- **energydc 错误/伪差异**：山西 2026-03/04 系统运行费虚高、2026-05 购电价错乱；山东 2026-09 漏单一制 220 并错位两部制；陕西 caption 映射 bug（已修 compare 脚本）；海南 2026-06 类别笔误 `100千瓦安`（已加规范化）；湖北 110kV 需量/容量合并格；单一制需量/容量合并格扩散（江苏、北京等）；陕西 "—" 写 0.0；TOU 解析把嵌套尖峰/深谷当重叠段或丢段（山东、甘肃、江西、湖南）。
- **覆盖差异**：energydc 有而我方无：黑龙江 5 个月、云南 7 个月、甘肃 2025-11/12、四川 2026-09（我方均因无官方可读原件而缺）。重庆 2025-11/2026-03、江西 2025-11、四川 2026-06 已于 2026-10-11 由 95598 文本 PDF 补齐。
- **未复核**：浙江 8 个 `ours_null` 深谷格、江苏 14 个 `ours_null` 尖峰格（第一轮归因 energydc 合并格扩散）【待核实】。

---

# 6. 待办与待决

## 6.1 缺失月份（官方渠道未取得可读原件，不估算）

| 区域 | 缺失月份 | 原因 | 下一步 |
|---|---|---|---|
| 黑龙江 heilongjiang | 2025-11、2025-12、2026-02、2026-03、2026-04（5 个） | 95598 P2019 黑龙江列表"功能正在维护升级"；hl.sgcc 栏目止于 2025-08；站内搜索无；无官方转载 | 定期复查 P2019；搜黑龙江市县政府"公共企事业单位信息公开"转载 |
| 云南 yunnan | 2025-11 … 2026-05（7 个） | 南网 API 只有 2026-06 起；yn.csg.cn 不可达；楚雄州发改委转载列表 403 | 浏览器试 fgw.cxz.gov.cn 列表（`https://fgw.cxz.gov.cn/zfxxgk/fdzdgknr/ggqsydw/yndwyxzrgscxgdj.htm`）与其他州市转载 |
| 甘肃 gansu | 2025-11、2025-12（2 个） | 95598 甘肃列表从 2026-01 开始 | 搜甘肃市县政府转载 |
| 四川 sichuan | 2026-09（1 个） | 95598 条目只有 .docx、无静态 URL | 浏览器下载 docx（无需登录）或找转载 |

## 6.2 数据缺陷（建议优先处理）

| 优先级 | 问题 | 处理 |
|---|---|---|
| ~~P0~~ 已修复 | tou_rule 主键冲突：anhui 与 shandong 同用 21000，zhejiang 与 henan 同用 22000；DB 中 anhui 仅剩 3/8 条、zhejiang 仅剩 4/8 条；Excel 已受影响，JSON 尚未（§1.6） | 改基数 → 删两省规则 → 重跑 anhui、zhejiang（`--skip-discover`）→ 复核 shandong=9、henan=5 → 重新导出 Excel；在幂等检查中加入全局规则计数 |
| ~~P1~~ 已修复 | 江苏 2026-02..05 两部制 220 千伏 `voltage` 截断为 `220千伏及`/`220千伏及以`（4 行） | 在 `jiangsu.py` 解析处修正截断后，先 `DELETE FROM tariff_month WHERE region_code='jiangsu' AND month BETWEEN '2026-02' AND '2026-05'` 再重跑（主键含 voltage，否则旧行残留） |
| P2 | `ocr_table.regulated_consensus` 默认按行数分期且会改文本月（chongqing 混有 3 个文本月） | 改用 `_north_util.consensus_ocr_only` 或显式日期分期；改后两遍哈希验证 |
| P2 | `pipeline/sources.csv` 多行过时（`hebei-north`、unknown、未核实文号等） | 按 §4 更新 |
| P3 | `pipeline/README.md` 部分状态过时（重庆仍写 10/12、83 行；安徽/浙江写 8 条规则） | 修 §1.6 后统一更新 |
| P3 | 河南模块 docstring / TOU_NOTES 对文本/OCR 月份的描述与 announcements.json 不一致；安徽 docstring 写 110/220 "from 2026-06" 与 DB（2026-08）不一致 | 修正文字 |
| P3 | 青海分时政策文号未获取；江西 463号 生效日未核实；冀北 1711号 effective_from 为空 | 浏览器查发改委原文 |

## 6.3 分时规则缺口

- **广西无 tou_rule**：2024 年动态调整的定稿正文无法从官方获取（征求意见稿时段：峰 16-24；平 0-1、7-12、14-16；谷 1-7、12-14）。下一步：浏览器访问广西发改委（fgw.gxzf.gov.cn）或钦州等市政府转载查找定稿文号与正文；找到前不得用征求意见稿入库。

## 6.4 用户待决事项（需用户拍板，未决前保持现状）

1. **江门 2026-08 起是否继续复制"珠三角6市"表**：现状 `guangdong-jiangmen` 2026-08..10 的行 = 珠三角6市表（与 `guangdong-prd5` 相同），因为公告已无单独江门表。可选：保持复制 / 2026-08 起江门停更并在前端合并到 prd5 / 改名。
2. **广西"其他"列**：第三周期（≤2026-07）表中"其他"（峰谷分时损益分摊）未入库，导致 21 条求和校验失败。可选：保持不存（现状）/ 计入 `system_op` / schema 加字段。
3. **云南系统运行费 "-"**：现状存 NULL（46 条 `components_missing`）。可选：保持 NULL / 存 0 / 另加说明字段。

## 6.5 工程待办

- **前端切换到 `data/json/<region>.json`**：`data/tou_2026-10.json` 是早期演示数据，来源注明"各省电网代理购电公告（经 energydc.cn 解析，2026-10-10 读取）"，**违反"energydc 不入库"约束，应停用**；前端代码位置未在本项目目录中找到【待核实】（`docs/plan.md` 称"Frontend reads data/json/<region>.json"）。
- **月度增量轮询自动化未建**：建议建一个每月 26 日、次月 1 日、次月 4 日（北京时间）运行的定时任务：对"实时发现"区域直接跑 `pipeline.run` 单月；对 seed 区域派浏览器任务抓 95598 P2019 新条目并追加 seed；之后全窗口 `--skip-discover` 复跑、导出 Excel、对账；失败写 `work/progress_monthly.md`。
- 西藏：2026-10 起市场化交易，按月复查 95598 西藏列表是否出现代理购电公告。
- 每次沙箱重置后重装 Python 依赖（§1.7）。

---

# 7. 附录

## 7.1 命令速查

```bash
cd /workspace/projects/55dbe641-d867-49ae-80e6-e219c2d53136
python -m pip install pdfplumber pypdfium2 pytesseract numpy pillow xlrd openpyxl pandas

# 单区域全流程 / 离线复跑
python -m pipeline.run --region <code> --from 2025-11 --to 2026-10
python -m pipeline.run --region <code> --from 2025-11 --to 2026-10 --skip-discover

# 全部区域离线复跑（顺序跑，避免锁库）
for r in $(sqlite3 data/tariff.db "select code from region order by code"); do
  python -m pipeline.run --region $r --from 2025-11 --to 2026-10 --skip-discover || echo "FAIL $r"; done

# 导出 Excel
python -m pipeline.export_xlsx --from 2025-11 --to 2026-10

# 对账
python -m pipeline.compare_energydc --region <code> [--no-fetch] [--budget 110]
python -m pipeline.compare_energydc --rebuild-summary

# 幂等哈希
python work/north_hash.py <code> [<code> ...]

# 状态查询
sqlite3 data/tariff.db "select region_code,count(distinct month),count(*) from tariff_month group by 1"
sqlite3 data/tariff.db "select region_code,count(*),min(id),max(id) from tou_rule group by 1"
sqlite3 data/tariff.db "select region_code,doc_no,effective_from,effective_to,source_url from policy_doc where kind='tou_policy'"
for f in data/validation_*.csv; do echo "$f $(($(wc -l <$f)-1))"; done

# 南网 API 探测
curl -s -X POST -H 'Content-Type: application/json' \
 -d '{"areaCode":"030000","level":"2","classId":"","keyword":"代理购电","pageNo":1,"pageSize":50,"version":"cn"}' \
 https://95598.csg.cn/ucs/ma/wt/searchService/queryInformationList

# 95598 omg-static 下载 + 类型判断
curl -sSL -o /tmp/x https://www.95598.cn/omg-static/<id> && head -c 4 /tmp/x | xxd
```

## 7.2 `seed.json` / `announcements.json` 结构

**seed.json**（各组略有差异，新增月份时照抄同省已有条目的字段）：

| 组/省 | 顶层键 | 每条 article 字段 |
|---|---|---|
| 西北（shaanxi、shaanxi-yulin、gansu、qinghai、xinjiang） | `note, listing, articles` | `month, title, article_url, publish_date, publish_datetime, file_url, file_ext` |
| 北方（beijing、jilin） | `source, articles` | `month, title, article_url, publish_date, publish_datetime, attachment_name, file_url[, file_ext]` |
| heilongjiang | `source, missing, articles` | `month, title, publish_date, article_url, file_url, all_files, file_ext` |
| mengdong | `source, articles` | `month, title, publish_date, article_url, file_url, file_ext, source_site` |
| 中部 shandong、hunan | `note, listing, articles` | `month, title, article_url, publish_date, file_url` |
| henan | 同上 | + `text_pdf_url, text_pdf_note` |
| hubei | 同上 | + `file_key, page`（合订本页码） |
| sgcc_cms 类（jibei、liaoning、ningxia、sichuan、tianjin、jiangxi、shanghai） | `note, listing/list_url, articles`（sichuan 另有 `missing`、`note_2026-06`；tianjin 有 `duplicates`；jiangxi 有 `image_only, not_found`；shanghai 有 `gathered_at`） | `month, title, article_url, publish_date, url_ts, files[]`（shanghai 无 url_ts） |
| guizhou | `note, announcements, missing` | **已废弃** |

安徽、浙江无 seed.json，URL 在模块 `SEED` 字典。

**announcements.json**（run.py 每次写回；一月一条）常见字段：`month, title, article_url, file_url, file_ext, publish_date, effective_from, effective_to, file_path, sha256, parse_method`；按需：`ocr_corrections`（OCR/多数投票修复日志）、`period_prices` / `period_purchase_prices`（甘肃/宁夏/新疆时段值）、`other_component_not_stored`（广西"其他"）、`alt_source`、`text_pdf_url/path/sha256`（河南）、`page`/`file_key`（湖北）、`class_id/info_id/preview_image/area_label`（南网）、`ocr_title/ocr_info_table/ocr_voltage_digits/header_check`、`img_urls/cms_img_urls/img_sha256`、`other_versions/superseded_files`、`avg_on_grid_price`（广西 D 校验）。

## 7.3 文件清单

| 路径 | 内容 |
|---|---|
| `pipeline/run.py`、`parse_common.py`、`build_json.py`、`export_xlsx.py`、`compare_energydc.py`、`schema.sql`、`README.md`、`sources.csv` | 主流程与说明 |
| `pipeline/sources/*.py`（40 个区域模块 + 7 个 util + 2 个 energydc 探测） | 区域适配器 |
| `pipeline/tessdata/{chi_sim,eng}.traineddata` | OCR 语言包 |
| `data/tariff.db` | SQLite（WAL） |
| `data/raw/<code>/` | 原件、seed.json、announcements.json、ocr_cache/ |
| `data/raw/_guangdong_notices/{gd,sz}/` | 广东/深圳公告共享缓存 |
| `data/raw/energydc/` | energydc 对账缓存（不入库） |
| `data/json/<code>.json` | 前端数据（40 个） |
| `data/validation_<code>.csv` | 校验失败（beijing 13、guangxi 21、ningxia 21、yunnan 46、zhejiang 5，其余 0） |
| `data/tou_2026-10.json` | 旧演示数据（energydc 来源，待停用） |
| `exports/分时电价_已入库_2025-11至2026-10.xlsx` | sheet：覆盖情况、电价明细、分时时段规则、说明 |
| `web/downloads/*.pdf`、`web/downloads/csg2/` | 分时政策原件副本 |
| `web/notes/energydc_assessment.md`、`web/notes/south_west_sources.md` | 评估与来源笔记 |
| `work/BRIEF_regions.md` | 区域 worker 通用简报 |
| `work/progress_*.md` | 各组进度日志（six_regions、cq_hebei、north、central、guangdong、zj_ah_fj、south_west、csg2、northwest、alt_pdfs、compare、compare2） |
| `work/compare/` | 对账输出、`our_errors.md`、`review_verdicts.json`、`evidence/` |
| `work/north_hash.py`、`work/gd_hash.py`、`work/*eyecheck.py`、`work/central/eyecheck.py` | 幂等/目视核对脚本 |
| `docs/plan.md` | 方案与用户决策 |
| `docs/HANDOFF_SOP.md` / `.docx` | 本文档 |

## 7.4 关键 URL 清单

**渠道入口**
- 95598 P2019 列表：https://www.95598.cn/osgweb/ipElectrovalenceStandard?partNo=P2019
- 95598 详情：https://www.95598.cn/osgweb/ipSupportDetailItems?releaseInfoId=<id>&partNo=P2019
- 95598 静态文件：https://www.95598.cn/omg-static/<id>
- 南网 API：https://95598.csg.cn/ucs/ma/wt/searchService/queryInformationList （POST）
- 南网下载：https://95598.csg.cn/ucs/ma/wt/business/downloadFiles?documentId=…&documentType=pdf&documentName=downloadFile
- 江苏：http://www.js.sgcc.com.cn/xxgk/gwntgdgs/djhsfbz/index.shtml
- 河北南网：http://www.he.sgcc.com.cn/html/main/col466/column_466_1.html
- 山西：http://www.sx.sgcc.com.cn/wbw/channels/c40145_1.html
- 重庆：http://www.cq.sgcc.com.cn/html/main/col2680/column_2680_1.html
- 上海：http://www.sh.sgcc.com.cn/html/main/col2017082164/column_2017082164_1.html
- 蒙东：http://www.md.sgcc.com.cn/html/md/gb/xxgkx/zcfgjsfbz/index.shtml
- 蒙西转载：https://www.zxbq.gov.cn/eportal/ui?pageId=6cffcf6c91144e8a83ef0649e7e45da2&currentPage=1&moduleId=c92754c6ad4248a396dce21019855d4d&staticRequest=yes
- 福建转载：http://www.dehua.gov.cn/zwgk/zdxxgk/ggqsy/gd/
- 安徽转载：https://whfgw.wuhu.gov.cn/fgyw/tzgg/
- 河南转载：https://www.pds.gov.cn/channels/38390.html
- 汕头镜像（核对）：https://www.shantou.gov.cn/cnst/ztzl/ggqsydw/gd/stgdj/fwxx/

**分时政策**

| 区域 | 文号 | URL |
|---|---|---|
| 安徽 | 皖发改价格〔2025〕302号 | https://www.ahsz.gov.cn/public/2655575/195697811.html |
| 北京 | 京发改规〔2023〕11号 | https://fgw.beijing.gov.cn/fgwzwgk/2024zcwj/bwgfxwj/202308/t20230821_3718725.htm |
| 重庆 | 渝发改规范〔2021〕14号 | https://fzggw.cq.gov.cn/zwgk/zfxxgkml/zcwj/xzgfxwj/sfzggwxzgfxwj/202112/t20211209_10119678.html |
| 福建 | 闽发改规〔2023〕8号 | https://fgw.fujian.gov.cn/zfxxgkzl/zfxxgkml/bwgfxwj/202311/t20231129_6309872.htm |
| 甘肃 | 甘发改价格〔2024〕424号 | https://www.95598.cn/omg-static/99307021080468606529101523659698 |
| 广东/深汕 | 粤发改价格〔2021〕331号 | http://www.jiangmen.gov.cn/jmfgj/gkmlpt/content/2/2405/post_2405749.html |
| 深圳 | 深发改〔2021〕1005号 | https://fgw.sz.gov.cn/zwgk/qt/tzgg/content/post_9493596.html |
| 贵州 | 黔发改价格〔2023〕481号 | https://95598.csg.cn/ucs/sr/info/06/a8d3146e14ed41d1ae18c8a7547c3cdc/20260730105307/60836cab0a254f06b07f214e1db42486.html |
| 贵州 2026-02 | （当月公告注2） | https://95598.csg.cn/ucs/sr/info/06/a8d3146e14ed41d1ae18c8a7547c3cdc/20260130093930/498d44adaf634369b0cdf3bfab4e8fa1.html |
| 海南 | 琼发改规〔2021〕18号 | https://95598.csg.cn/ucs/sr/info/hn/07-firm/211223112311/52b72e0f866e4826a0508bee193c8298.html |
| 河北南网 | 冀发改能价〔2025〕1115号 | 石家庄 sjz.gov.cn 附件（完整 URL 见 `hebei_south.py` 的 `TOU_1115`） |
| 黑龙江 | 黑发改价格函〔2024〕291号 | https://www.95598.cn/omg-static//omg-static/99301061503136265824901857003111.pdf |
| 河南 | 豫发改价管〔2024〕283号 | https://fgw.henan.gov.cn/2024/05-29/3000232.html |
| 湖北 | 鄂发改价管〔2024〕77号 / 〔2025〕86号 | https://fgw.hubei.gov.cn/fbjd/zc/gfwj/gf/202404/t20240401_5141591.shtml ; https://fgw.hubei.gov.cn/fbjd/zc/gfwj/gf/202504/t20250411_5612469.shtml |
| 湖南 | 湘发改价调规〔2025〕385号 | http://fgw.hunan.gov.cn/fgw/xxgk_70899/zcfg/dfxfg/202507/t20250702_33727950.html |
| 江苏 | 苏发改价格发〔2025〕426号 | http://fzggw.jiangsu.gov.cn/art/2025/4/30/art_284_11557001.html |
| 江西 | 赣发改价管〔2025〕463号 | http://www.jx.sgcc.com.cn/html/main/col2786/2025-06/25/20250625152518539619489_1.html |
| 冀北 | 冀发改能价〔2023〕1711号 / 〔2025〕1275号 | https://www.95598.cn/omg-static/99306111884397824210202129032439 ; https://www.hbkc.gov.cn/art/2025/12/4/art_5989_1095355.html |
| 吉林 | 吉发改价格〔2025〕11号 | https://www.95598.cn/omg-static/99305070811275452078400030458563.pdf |
| 辽宁 | 辽发改价格〔2023〕441号 | https://fgw.ln.gov.cn/fgw/index/tzgg/2023083014061818174/index.shtml |
| 蒙东 | 内发改价费字〔2023〕1631号 | https://www.95598.cn/omg-static/99308051060403522315301243350380 |
| 蒙西 | 内发改价费字〔2023〕1630号 | https://fgw.xlgl.gov.cn/fgw/ywgz/zdxm/tzgg/2024010409440178739/index.html |
| 宁夏 | 宁发改运行〔2024〕952号 / 宁发改价格（管理）〔2026〕79号 | http://www.nx.sgcc.com.cn/html/files/2025-10/28/20251028174546362481154.pdf ; https://fzggw.nx.gov.cn/tzgg/202602/t20260212_5170559.html |
| 青海 | （文号待核实，2024-04-09） | https://www.95598.cn/omg-static/99309241946254462759200214872640 |
| 陕西/榆林 | 陕发改价格〔2025〕1034号 | https://sndrc.shaanxi.gov.cn/zfxxgk/zc/fgwj/sfzggwwj/2025/202507/t20250723_3546317.html |
| 山东 | 鲁发改价格〔2023〕914号（2026 年时段公告）/ 鲁发改价格函〔2026〕68号 | https://www.95598.cn/omg-static/99106241082992543400401950838465 ; https://www.95598.cn/omg-static/99109111502938089123900953785807 |
| 上海 | 沪发改价管〔2022〕50号 / 〔2024〕28号 | https://fgw.sh.gov.cn/fgw_jggl/20221216/e2652e3ab7ee49438d6e82af8880b160.html ; https://fgw.sh.gov.cn/fgw_jggl/20240613/c753f8153e15492898c2ea3737ee93d5.html |
| 山西 | 晋发改商品发〔2021〕479号 / 〔2026〕15号 | http://www.sx.sgcc.com.cn/homepage/wbw/docs/2025/10/28/7ae79c1ac3b24ddb9695f393bf47a859.pdf ; https://fgw.shanxi.gov.cn/sxfgwzwgk/sxsfgwxxgk/xxgkml/tz/202602/t20260228_10069158.shtml |
| 四川 | 川发改价格〔2025〕185号 | https://fgw.sc.gov.cn/sfgw/qtwj/2025/4/30/8f5b20c4de7e4df48faa04935e512111.shtml |
| 天津 | 津发改价管〔2025〕277号 | https://www.95598.cn/omg-static//omg-static/99309111418199724687701554929817.pdf |
| 新疆 | 新发改规〔2023〕11号 | https://xjdrc.xinjiang.gov.cn/xjfgw/c108361/202312/e5c0f50c7028486ebe36ea253829b340.shtml |
| 西藏 | 藏政办发〔2023〕28号 | https://www.95598.cn/omg-static/99308100976112223958902124993974 |
| 云南 | 云发改价格〔2024〕924号 / 〔2026〕51号 | https://shidian.gov.cn/info/11678/3666103.htm ; https://95598.csg.cn/ucs/ma/wt/business/downloadFiles?documentId=bmZkd195eF90eWZ3L3Nkay9vc3NGaWxlLzZhNmM2NzhhZjYyNzQ3MzZhNWQwYjc0NWZkYzBiNTk3L1BERg==&documentType=pdf&documentName=downloadFile |
| 浙江 | 浙发改价格〔2024〕21号 / 〔2026〕112号 | https://www.fh.gov.cn/art/2024/2/2/art_1229704397_59137762.html ; https://fzggw.zj.gov.cn/col/col1229123351/art/2026/art_8282044c7b1b469c82f5fd9d2c27a41d.html |
| 广西 | 无（未入库） | — |

**energydc（仅对账）**：https://energydc.cn/sitemap/sitemap_policy_1.xml ；https://energydc.cn/edc/api/v1/eprice/policy/<uuid> ；条款 https://energydc.cn/terms
