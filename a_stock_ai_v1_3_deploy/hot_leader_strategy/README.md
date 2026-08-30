# Hot Leader v1

独立的 A 股热点题材龙头研究与本地模拟模块。策略含两种可审计模式：趋势跟随和分歧回强；不做涨停排队打板，不连接券商。

状态、缓存、账户与回测均在 `hot_leader_strategy/local_data/hot_leader.sqlite`，不会读写 hybrid_alpha、ETF 或 Q-GARP 的账户。

```bash
python -m hot_leader_strategy --refresh --start 20240101 --end 20241231 --limit 800
python -m hot_leader_strategy --signal-once
python -m hot_leader_strategy --backtest --start 20240101 --end 20241231
python -m hot_leader_strategy --paper-once
```

默认 `HOT_LEADER_PAPER_ENABLED=false`、`HOT_LEADER_PUSH_ENABLED=false`、`HOT_LEADER_DRY_RUN=true`。推送仅对独立模拟账户实际成交订单发送，观察名单永不推送。

启用 `HOT_LEADER_INTRADAY_ENABLED=true` 后，系统会在北京时间开盘时段按 `HOT_LEADER_INTRADAY_INTERVAL_MINUTES`（默认 2 分钟）更新上一交易日计划候选与独立持仓的实时价格。它只在首次盘中买入、卖出或风控成交时推送；相同方向事件进入冷却期，不会推送观察清单。盘中层是价格确认与持仓风控，不会把日线热点模型改成高频全市场扫描。

题材热度使用可追溯的行业横截面代理，而不使用事后才知道的概念标签；因此历史回测会强制要求点时点股票池和至少 98% 的复权因子覆盖，数据不足时拒绝产出收益数字。`HOT_LEADER_MAX_THEME_EXPOSURE` 默认 10%，`HOT_LEADER_MAX_SINGLE_WEIGHT` 默认 5%，可分别调整。
