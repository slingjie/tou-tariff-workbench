# 分时电价工作台 / 综合电价计算器

中国 40 个区域（国网 28、南网 11、内蒙古电力 1）电网企业代理购电工商业用户电价与分时时段规则的采集管线，以及两个静态页面。

数据只来自免费官方公开渠道（国网 95598.cn、南网 95598.csg.cn、省电力公司官网、发改委及政府网站转载）。energydc.cn 只用于对账，不入库。尖峰、深谷价只存官方价表印出的值，不推算。

## 目录
- `pipeline/`：采集、解析、校验、入库、导出代码。每个区域一个 `pipeline/sources/<code>.py`。
- `data/tariff.db`：SQLite 正式库（约 3713 行电价、145 条分时规则，2025-11 至 2026-10）。
- `data/json/<code>.json`：各区域前端数据；`data/validation_<code>.csv`：校验结果。
- `work/web/tariff_bundle.json`：两个页面共用的数据包。
- `sites/workbench/`：分时电价工作台（静态页，打开 `index.html`）。
- `sites/calculator/`：综合电价计算器（日内时间窗、日类型、按实际日历加权）。
- `exports/`：Excel 导出。
- `docs/HANDOFF_SOP.md`：完整交接 SOP，每个区域的来源、表格结构、分时文号和已知问题。
- `skills/china-tariff-pipeline/SKILL.md`：给其他 agent 用的 SOP 摘要。

## 常用命令
```bash
pip install pdfplumber numpy pytesseract pypdfium2 pillow pandas openpyxl xlrd   # tesseract 需 chi_sim
python -m pipeline.run --region <code> --from 2025-11 --to 2026-10
python -m pipeline.run --region <code> --from 2025-11 --to 2026-10 --skip-discover
python -m pipeline.compare_energydc --region <code>
python -m pipeline.export_xlsx
```

## 不在仓库里的内容
原始 PDF、XLS、图片（`data/raw/`）和中间文件（`work/`）约 1.4GB，超出 GitHub 限制，未上传，完整副本在 Google Drive 文件夹「分时电价工作台_完整包_2026-10-11」中。离线复跑（`--skip-discover`）需要先把 `data/raw/` 放回原位。

## 本地预览页面
```bash
cd sites/calculator && python -m http.server 8000
```
数据包已内联进 `index.html`，可直接双击打开；更新数据后需重新内联 `work/web/tariff_bundle.json`。
