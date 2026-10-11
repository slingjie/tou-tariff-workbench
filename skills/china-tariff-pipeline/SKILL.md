# 中国各省代理购电 / 分时电价采集管线 SOP

用途：采集 40 个区域代码（28 国网、11 南网、1 内蒙古电力）的电网企业代理购电工商业用户电价（尖峰/高峰/平/低谷/深谷、电压等级、用电分类、单一制/两部制、需量/容量电价）和分时时段规则，校验、入库、导出，每月增量更新，并与 energydc.cn 只读对账。

完整交接文档：`docs/HANDOFF_SOP.md`。动某一省前先读 §4 该省小节；§6 是待办与待决。

## 硬性约束
1. 只用免费官方来源：国网/南网/省电力公司官网、95598.cn、95598.csg.cn、发改委、政府官网转载。
2. 不登录、不绕付费墙、不打受保护后端。
3. energydc.cn 只用于发现和对账，绝不入库（其 ToS §4 禁止复用）。
4. 尖峰/深谷价只存官方价表明确印出的值，不按规则推算；缺失月份不估算。
5. 政策冲突以最新正式文件为准（浙江：2026-07-01 起浙发改价格〔2026〕112号）。
6. 江苏单一制 100 千伏安上下的拆分放在 `category`。
7. 官方表 `—`/`-` 存 NULL。

## 模块接口
每区域一个 `pipeline/sources/<code>.py`（`-` 写成 `_`），导出 `REGION` 与 `discover()` / `download()` / `parse()`，可选 `postprocess()`、`extra_validate()`、`tou_rules()`。共享适配器：`sgcc_cms.py`、`ocr_table.py`、各组 `_*_util.py`。`TOU_ID_BASE` 全局唯一，新区域从 81000 起。

## 命令
```bash
python -m pipeline.run --region <code> --from 2025-11 --to 2026-10
python -m pipeline.run --region <code> --from 2025-11 --to 2026-10 --skip-discover
python -m pipeline.compare_energydc --region <code>
python -m pipeline.export_xlsx
```

## 来源渠道
- 国网 95598.cn：列表页只能用浏览器打开；文件在 omg-static，可直接 curl。URL 登记在 `work/urls_95598.json`。
- 南网 95598.csg.cn：`POST /ucs/ma/wt/searchService/queryInformationList`，areaCode 广东 030000、深圳 090000、广西 040000、云南 050000、贵州 060000、海南 070000。
- 省电力 CMS：下载用 curl，不用 Python requests。
- 政府转载：福建 dehua.gov.cn、蒙西 zxbq.gov.cn、河南 pds.gov.cn、江西 xunwu.gov.cn、安徽芜湖。

## 每月增量
原件多在上月 24-31 日发布。每区域跑 run → 看 validation → 跑两遍比对 → 对账 → 导出 Excel → 重建前端数据包。

## OCR 校验
组件求和、浮动比例反算、同监管周期输配电价多数投票；求和通过不代表正确（重庆 2026-02 线损/基金串读），必须人工抽查。

## 当前缺口（2026-10）
黑龙江 5 个月、云南 7 个月、甘肃 2025-11/12、四川 2026-09 缺失；广西无现行分时规则；西藏只有分时规则。
