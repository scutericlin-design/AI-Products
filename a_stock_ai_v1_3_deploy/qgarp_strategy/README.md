# Q-GARP 独立 A 股策略

这是一个独立于 `hybrid_alpha`、现有多策略、ETF 策略和原有模拟盘的本地研究/模拟盘模块。

它使用独立数据库 `qgarp_strategy/local_data/qgarp.sqlite`，不会连接券商，也没有任何实盘下单代码。

## 本地使用

```bash
python -m qgarp_strategy --refresh
python -m qgarp_strategy --signal-once
python -m qgarp_strategy --paper-once
python -m qgarp_strategy --account
python -m qgarp_strategy --backtest --start 20170101 --end 20261231
python -m qgarp_strategy --hydrate-adjustment-factors-by-date --start 20180101 --end 20260801 --limit 20
```

Dashboard 的只读入口是 `/qgarp`，API 是 `/api/qgarp-dashboard`。

## 数据与风控

- `QGARP_TUSHARE_TOKEN`（或 `TUSHARE_TOKEN`）是财务与公告时间点数据的必需来源。
- `QGARP_TUSHARE_BASE_URL`（或 `TUSHARE_BASE_URL`）优先使用现有 TuShare 中转。
- AKShare 仅在 TuShare 日线不可用时提供价格备用，绝不补造财务、预期或公告因子。
- 没有可用时间不晚于决策日的财务数据时，股票不能进入买入池。
- 模拟盘执行 100 股整数倍、T+1、佣金、卖出印花税与滑点。

## 默认安全状态

```text
QGARP_PAPER_ENABLED=false
QGARP_PUSH_ENABLED=false
QGARP_DRY_RUN=true
```

即使开启 `QGARP_PUSH_ENABLED=true`，也仅会发送已成交的 Q-GARP 模拟盘订单；观察名单、HOLD 和拒单不会推送。

## 参数化研究档案（v2）

默认档案 `quality_value_momentum` 使用行业内排名的质量、成长、价值、60/120 日跳过最近 20 日的中期动量、低波因子；流动性默认是硬门槛而非收益因子。所有参数都会写入每次信号和回测的快照，因而可以复现，而不是覆盖旧结果。

内置的研究候选档案只有三个：`quality_value_momentum`（均衡）、`quality_value_defensive`（更高质量/价值/低波）和 `quality_momentum`（质量加趋势）。这是用于样本外比较的少量、可解释假设，不是为了搜索出历史曲线最好看的参数。

常用环境变量：

```text
QGARP_STRATEGY_PROFILE=quality_value_momentum
QGARP_FACTOR_QUALITY_WEIGHT=0.30
QGARP_FACTOR_GROWTH_WEIGHT=0.10
QGARP_FACTOR_VALUE_WEIGHT=0.25
QGARP_FACTOR_MOMENTUM_WEIGHT=0.25
QGARP_FACTOR_LOW_VOL_WEIGHT=0.10
QGARP_SELECTION_TOP_PCT=0.15
QGARP_MIN_ROE=5
QGARP_MAX_DEBT_TO_ASSETS=75
QGARP_REBALANCE_MONTHS=1
QGARP_DAILY_EXIT_ENABLED=false
QGARP_BACKTEST_MIN_ADJ_COVERAGE=0.98
```

月度截面策略默认禁用日内止盈、跟踪止损；如需启用，必须显式设定 `QGARP_DAILY_EXIT_ENABLED=true`，且只能作为灾难性保护。历史价格复权因子须通过独立的按交易日批量补全命令写入缓存后，回测才可被标为“复权价格研究结果”。回测默认要求测试期与动量预热期的复权覆盖率至少 98%，不足时拒绝生成结果。补全任务每次处理有限日期，失败后会从第一个覆盖率不足 98% 的交易日自动续跑。
# Q-GARP v3 research gate

`v3_research.py` is independent from the existing v2 signal, paper account and
notification paths.  It is research-only: it cannot place paper orders or send
messages.  Use it to verify factor rank IC and top-minus-bottom spreads before
authorizing a separate v3 portfolio backtest.

```bash
python -m qgarp_strategy --v3-diagnostics --start 20180101 --end 20231231
python -m qgarp_strategy --v3-signal-once
```

V3 accepts only `QGARP_V3_*` parameters. It never reads or changes the v2
profile parameters. It refuses incomplete fundamental fields, incomplete
adjusted-price history and missing point-in-time universe membership.
