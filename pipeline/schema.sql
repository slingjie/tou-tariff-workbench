-- 分时电价工作台 数据结构 v1
CREATE TABLE region (
  code TEXT PRIMARY KEY,          -- e.g. jiangsu, guangdong-prd5, hebei-north
  name TEXT NOT NULL,             -- 江苏 / 广东珠三角五市
  grid TEXT NOT NULL,             -- sgcc / csg / impc(内蒙古电力)
  province TEXT NOT NULL
);
CREATE TABLE policy_doc (
  id TEXT PRIMARY KEY,            -- sha1 of source url
  region_code TEXT REFERENCES region(code),
  kind TEXT NOT NULL,             -- agency_price(代理购电公告) / tou_policy(分时政策) / tdp(输配电价)
  title TEXT, doc_no TEXT,        -- 文号
  publish_date TEXT, effective_from TEXT, effective_to TEXT,
  source_url TEXT NOT NULL, file_path TEXT, sha256 TEXT,
  fetched_at TEXT
);
CREATE TABLE tariff_month (
  region_code TEXT REFERENCES region(code),
  month TEXT NOT NULL,            -- YYYY-MM
  category TEXT NOT NULL,         -- 一般工商业 / 大工业 / 工商业(合并口径的省)
  tariff_type TEXT NOT NULL,      -- 单一制 / 两部制
  voltage TEXT NOT NULL,          -- 原文电压等级: 不满1千伏 / 1-10(20)千伏 / 35千伏 / 110千伏 / 220千伏及以上
  voltage_rank INTEGER,           -- 1..5 用于排序
  energy_price REAL,              -- 非分时电度电价 元/kWh
  purchase_price REAL,            -- 代理购电价格
  line_loss REAL,                 -- 上网环节线损
  tdp REAL,                       -- 电度输配电价
  system_op REAL,                 -- 系统运行费用折价
  gov_fund REAL,                  -- 政府性基金及附加
  sharp REAL, peak REAL, flat REAL, valley REAL, deep_valley REAL,  -- 分时电度电价 元/kWh, NULL if 无
  demand_price REAL,              -- 元/kW·月
  capacity_price REAL,            -- 元/kVA·月
  doc_id TEXT REFERENCES policy_doc(id),
  PRIMARY KEY (region_code, month, category, tariff_type, voltage)
);
CREATE TABLE tou_rule (
  id INTEGER PRIMARY KEY,
  region_code TEXT REFERENCES region(code),
  effective_from TEXT, effective_to TEXT,  -- YYYY-MM-DD
  months TEXT NOT NULL,           -- JSON array of 1..12 this rule applies to
  day_type TEXT NOT NULL DEFAULT 'normal', -- normal / holiday / weekend
  segments TEXT NOT NULL,         -- JSON [[ "00:00","07:00","valley"], ...] types: sharp/peak/flat/valley/deep_valley
  doc_id TEXT REFERENCES policy_doc(id)
);
