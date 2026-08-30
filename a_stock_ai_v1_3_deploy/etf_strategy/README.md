# 本地 ETF 策略研究模块

这是独立于现有股票系统的本地研究与决策支持模块。它不会导入或调用：

- 股票盘中循环、APScheduler、飞书推送
- 现有模拟盘账户或订单日志
- Docker、Dashboard、云端服务器
- 任何真实或模拟下单接口

模块仅把 TuShare ETF 数据保存在本机 `etf_strategy/local_data/etf_strategy.sqlite`，并输出 JSON 格式的人工交易计划。

## 策略实现

基于“五福闹新春”的核心思路，保留：

- 全市场 ETF 选池：TuShare `etf_basic`，含境内与 QDII ETF
- 25 日加权趋势动量：年化趋势收益乘正确加权 R²
- MA10、近三日深跌、流动性、波动率过滤
- 四个 A 股指数 MA10 市场状态；弱市时只保留全球/QDII/商品候选
- 每个跟踪指数只保留成交最活跃的 ETF，避免同质 ETF 重复占位

并主动修复了原方案的高风险部分：

- 不再使用快照标签错配的上午卖出信号
- 不再尾盘强制买入；分钟数据不可用或趋势未确认时保持现金
- 默认最高目标仓位 70%，弱市 45%，而不是满仓单 ETF
- 二级市场价格相对基金净值溢价过高时拒绝买入
- AI 只可否决已筛出的计划，不能新增 ETF、加大仓位或跳过硬风控

## 本地配置

仅在本机终端设置环境变量，不要把密钥写进代码：

```bash
export TUSHARE_TOKEN='your_token'
export TUSHARE_BASE_URL='https://your-tushare-proxy.example'

# 可选：MiniMax 只做风险复核，默认关闭
export ETF_MINIMAX_ENABLED=true
export MINIMAX_API_KEY='your_key'
export MINIMAX_ENDPOINT='https://your-minimax-proxy.example/v1/chat/completions'
```

TuShare `etf_basic`、实时分钟、份额规模等接口受账户权限影响。数据端不可用时命令会明确报错或标注降级，绝不伪造行情或把缺失数据视作通过。

## 命令

在 `a_stock_ai_v1_3_deploy` 目录运行：

```bash
# 1. 下载全市场 ETF 基础信息、近 120 个自然日的 ETF 日线和指数日线到本地 SQLite
python -m etf_strategy refresh

# 2. 仅用本地缓存日线生成计划
python -m etf_strategy decide --account-value 1000000

# 3. 把现有持仓带入，生成“持有/切换/卖出”人工计划
python -m etf_strategy decide --current-symbol 510300.SH --account-value 1000000

# 4. 额外做分钟趋势确认与基金净值折溢价检查
python -m etf_strategy decide --confirm-intraday --enrich-nav

# 5. 同一轮本地刷新后生成计划；不会发送消息或下单
python -m etf_strategy run --account-value 1000000

# 6. 只使用本地缓存数据的研究回放：当日收盘产生信号、下一交易日开盘成交
python -m etf_strategy backtest --start-date 20240101 --end-date 20261231 --cost-bps-per-side 10
```

`--with-ai` 只有在 `ETF_MINIMAX_ENABLED=true` 时才会请求 StepFun，并在失败时自动尝试 MiniMax。仅当模型成功返回 `HOLD` 或 `REJECT` 时才否决正向计划；两者无配置、响应异常或无结构化 JSON 时，保留 ETF 策略原信号、原仓位和硬风控，并在 SQLite 与成交推送中标记 AI 降级执行。

## 数据与边界

- `etf_basic`：全市场 ETF 名单、跟踪指数、交易所、QDII 类型、上市状态。
- `etf_daily` 优先、`fund_daily` 兼容回退：ETF 日线和成交额。
- `index_daily`：市场状态指数日线。
- `fund_nav`：仅对最终候选可选查询，检查二级市场折溢价。
- `rt_etf_min_daily`：仅对最终候选可选查询，确认最近 30 分钟趋势。
- `fund_etf_spot_em`：文件静态池在 13:08 优先使用批量实时快照；覆盖率低于 `ETF_MINUTE_BATCH_SPOT_MIN_COVERAGE` 时拒绝逐只大规模补拉，避免超时风暴。

策略结果是研究和人工决策支持，不是收益保证，也不会自动交易。历史回放尤其要关注 ETF 上市/退市时点、折溢价、不同 ETF 的 T+0/T+1 规则、涨跌停与实际买卖价差。
