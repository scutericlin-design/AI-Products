# A股市场实时感知引擎 v1.9

v1.9 的目标是在不同市场风格下，给稳健、优质成长和热点龙头策略分配不同预算，并保持组合层的统一风控。

## v1.9 多策略动态配置

v1.9 保留现有稳健策略，并新增两个独立策略：

- `robust_hybrid`：原有质量、趋势、情绪与可交易性综合策略，作为稳健核心。
- `quality_growth`：使用 TuShare 财务指标、行业分类、估值和盘中可交易性筛选中长期候选。财务数据不可用或缓存过期时只观察，不会假装成基本面买入信号。
- `hot_leader`：只在结构性或趋势市场中以小仓观察强势龙头；涨停、接近涨停、低流动性标的一律不推荐。

市场风格由指数状态、上涨广度、情绪分、恐慌分和涨停热度共同判断，状态连续确认后才切换，`risk_off` 可立即生效。默认配置是影子运行：多策略组合写入 Dashboard、SQLite 和模拟盘，但飞书仍使用已运行的稳健策略，避免升级当日改变你收到的盘中建议。

```text
MULTI_STRATEGY_ENABLED=true
MULTI_STRATEGY_MODE=shadow      # shadow: 模拟盘/Dashboard；active: 组合策略接管飞书
MULTI_STRATEGY_PAPER_ENABLED=true
MULTI_STRATEGY_MAX_EXPOSURE=0.85
MARKET_REGIME_CONFIRM_CYCLES=2
FUNDAMENTAL_CACHE_TTL_HOURS=24
QUALITY_GROWTH_INDUSTRIES=半导体,通信,软件,人工智能,电力设备,新能源,高端制造,医药
```

在策略分年度、滚动样本外回测和一段时间的模拟盘结果均达标前，不应把 `MULTI_STRATEGY_MODE` 改为 `active`。系统是决策支持与模拟研究工具，不承诺收益，也不自动下实盘委托。

多策略从开始运行起会将已去重的组合信号写入回测池；可单独回放这些真实产生过的组合信号：

```bash
python -m app.main --backtest-multi --backtest-days 60 --holding-days 5
```

该回放只评估系统已经生成过的组合信号，避免用未来数据伪造历史决策。较长周期的历史研究仍需使用点时点财务数据、停牌/涨跌停成交约束和滚动样本外验证，不能将盘中实时策略直接套到历史上宣称有效。

## 终局收束

做到这一步后，这个系统已经具备：

- 准量化实时决策系统的基础闭环
- 工作日开盘期间长期运行
- TuShare 数据接入
- AKShare 多级备份数据源：东方财富快照、新浪快照、最近日线兜底
- MiniMax 盘中感知分析
- 市场情绪因子：广度、涨跌强弱、涨停/跌停压力、恐慌分、成交活跃度、覆盖度
- 可买性过滤：涨停、接近涨停、流动性不足不推荐
- 基础质地过滤：ST/退市风险、价格异常、振幅过大先剔除
- Top 推荐：默认每轮最多推送 3 只，并给出买入区间、最高追价和止损
- 强制风控处理
- 飞书 Webhook 推送
- 盘中防轰炸：重复推荐不推送，默认 60 分钟最多补一条状态摘要
- 盘中心跳：无推荐时约每 15 分钟最多推一条“系统正常运行”状态
- SQLite 日志沉淀
- 可选盘后复盘模块：默认关闭，不自动生成或推送文章
- 静默自学习：每天体检，只有 TuShare 归因和 MiniMax 复核都支持时才安全微调参数，不自动改代码
- v1.7 模拟盘：本地纸面账户、T+1 可卖数量、费用、滑点和一手 100 股约束
- v1.7 信号回放回测：用历史 BUY 推荐向后检验收益、胜率、回撤、止损率
- v1.7 质地分层组合研究：A/B/C 分层仓位、底仓 T、后续加仓、分批止盈和移动止盈
- v1.8 只读 Dashboard：实时信号、推荐池、模拟盘、交易记录、策略参数、止盈止损和系统健康
- 后续策略持续优化和扩展能力

## 运行闭环

每 5 分钟，在 A 股工作日开盘期间执行：

```text
拉数据(TuShare中转 -> TuShare新浪 -> AKShare多级备份) -> 更新状态 -> 计算市场情绪 -> 更新龙头 -> 候选池排序 -> 调 MiniMax -> 风控处理 -> 飞书推送 -> 写日志 -> 可选模拟盘 -> 静默自学习
```

休市、午休和周末自动跳过。
法定节假日和调休交易日可通过 `A_SHARE_HOLIDAYS`、`A_SHARE_EXTRA_TRADING_DAYS` 配置，格式为逗号分隔的 `YYYY-MM-DD`。
系统所有交易判断、调度任务、飞书消息时间和新写入日志均使用北京时间 `Asia/Shanghai`。

盘后复盘文章模块已默认关闭。只有设置 `REVIEW_ENABLED=true` 后，才会在工作日 20:00 执行：

```text
读取当日日志 -> 汇总盘中状态和推荐结果 -> MiniMax 写复盘文章 -> 飞书推送 -> 写复盘日志
```

## 生产配置

生产环境不要把密钥写入代码。复制 `.env.example` 为 `.env`，填入：

- `TUSHARE_TOKEN`
- `TUSHARE_BASE_URL`：如果使用 TuShare 中转服务，填中转地址，例如 `https://teajoin.com`
- `AKSHARE_ENABLED`：默认 `true`，当前置数据源无数据时启用 AKShare 备份
- `MINIMAX_API_KEY`
- `FEISHU_WEBHOOK`

本地验证可保持 `DRY_RUN=true`。真实盘中感知运行时设置：

```text
DRY_RUN=false
FEISHU_ENABLED=true
SEND_IN_DRY_RUN=false
```

无行情数据、MiniMax 异常或配置缺失时，系统会降级为 `HOLD` / `NO_DATA`，不会生成正向买入感知信号。

## v1.7 回测和模拟盘

v1.7 新增的是验证层，不是实盘交易层。系统不会连接券商，不会发真实委托。

模拟盘默认关闭，避免影响当前盘中分析和飞书推送：

```text
PAPER_TRADING_ENABLED=false
PAPER_INITIAL_CASH=1000000
PAPER_MAX_POSITION_PCT=0.12
PAPER_SLIPPAGE_PCT=0.001
PAPER_COMMISSION_RATE=0.00025
PAPER_MIN_COMMISSION=5
PAPER_STAMP_DUTY_RATE=0.0005
PAPER_LOT_SIZE=100
```

手动查看或运行模拟盘：

```bash
python -m app.main --paper-account
python -m app.main --simulate-once
python -m app.main --paper-reset
```

如果以后设置 `PAPER_TRADING_ENABLED=true`，系统会在每轮盘中推送和写日志完成后，额外把同一轮最终信号放进纸面账户撮合。模拟失败只写日志，不会改变推荐和推送结果。

手动运行信号回放回测：

```bash
python -m app.main --backtest
python -m app.main --backtest --backtest-days 60 --holding-days 5
```

回测逻辑会读取 SQLite 里的历史 `BUY` 推荐，使用推荐时的买入价、仓位、止损价，向后看指定交易日表现，并写入 `backtest_runs` 表。它用于检验系统真实发过的信号，不伪造历史推荐。

手动运行历史日线研究回测：

```bash
python -m app.main --historical-backtest --historical-days 180 --strategy-profile optimized
python -m app.main --historical-backtest --historical-days 180 --strategy-profile quality_t
```

`quality_t` 是 v1.7 的质地分层组合模式：

- A 类质地票：初始小仓，可加仓，可做底仓 T，止损止盈更宽
- B 类动量票：中等仓位，纪律止盈止损
- C 类短线票：小仓快进快出
- 做 T 只用于已有底仓，日线回测为近似模拟，不代表真实逐笔成交顺序

## v1.8 Dashboard

Dashboard 是只读监控台，不提供实盘下单、模拟下单、参数修改或任何 POST 写入接口。它读取 SQLite 日志、纸面账户文件和回测结果，用来观察系统状态。

Dashboard 默认启用登录保护：

```text
DASHBOARD_AUTH_ENABLED=true
DASHBOARD_AUTH_USERNAME=admin
DASHBOARD_AUTH_PASSWORD_HASH=<pbkdf2_sha256:iterations:salt:digest>
DASHBOARD_AUTH_SECRET=<random session signing secret>
DASHBOARD_SESSION_SECONDS=28800
```

生成密码哈希：

```bash
python -c "from dashboard.auth import make_password_hash; import getpass; print(make_password_hash(getpass.getpass()))"
```

未登录访问页面会跳转到 `/login`，未登录访问 Dashboard API 会返回 `401 authentication_required`。`/api/ping` 保持公开，用于容器健康检查。

浏览用户申请流程：

- 申请入口：`/a-stock-dashboard/apply`
- 后台入口：`/a-stock-dashboard/admin`
- 访客提交邮箱、姓名、密码和备注
- 管理员批准后，该邮箱可用申请时填写的密码登录
- 管理员可在后台批准、拒绝、停用或重新启用用户
- 管理员账号仍由 `DASHBOARD_AUTH_USERNAME` 和 `DASHBOARD_AUTH_PASSWORD_HASH` 控制

本地运行：

```bash
python -m dashboard.server
```

Docker Compose 会额外启动 `a-stock-dashboard-v1-8` 服务，默认端口：

```text
DASHBOARD_PORT=8080
```

页面包含：

- 实时总览：整体信号、仓位、风险、市场阶段、系统健康
- 推荐池：股票代码、名称、突破/回踩模式、买入区间、最高追价、止损、止盈观察、移动止盈
- 模拟盘：权益、现金、持仓、T+1 可卖数量、浮盈浮亏
- 交易记录：模拟订单、成交/拒单、拒单原因、价格、数量
- 策略说明：市场阶段、选股模式、质地过滤、情绪风控、MiniMax 二次确认、模拟盘规则
- 回测曲线和最近运行周期

## v1.5 推荐规则

系统不会直接说“按实时价无脑买”。推荐消息会同时给：

- 股票代码和股票名称
- 当前价
- 建议买入区间
- 最高追价
- 止损价
- 推荐理由
- 风险标签

默认每轮最多推送 `MAX_PUSH_STOCKS=3` 只。已经涨停、接近涨停、卖一价超过追价上限、成交额不足、ST/退市风险和基础质地不过关的股票不会进入买入推荐。

如果没有推荐，默认不推送完整推荐消息，避免盘中消息过多。`PUSH_HEARTBEAT_ENABLED=true` 时，系统会按 `PUSH_HEARTBEAT_INTERVAL_MINUTES` 的间隔推送低频心跳，告诉你系统正常但暂无买入推荐。可用 `PUSH_NO_RECOMMENDATION=true` 打开每轮“无推荐原因提醒”，一般不建议打开。

## 市场情绪风控

系统会在每轮行情后计算一份 `market_sentiment`：

- `sentiment_score`：0-100 的情绪分
- `risk_appetite`：`risk_on`、`neutral`、`risk_off`、`panic`
- `trade_permission`：`BUY_ALLOWED`、`LIGHT_ONLY`、`NO_BUY`
- `panic_score`：恐慌分，达到阈值后强制转防守
- `coverage_level`：样本覆盖度，覆盖不足时自动降低情绪权重和仓位

默认规则：

```text
SENTIMENT_ENABLED=true
SENTIMENT_MIN_BUY_SCORE=53
SENTIMENT_RISK_OFF_SCORE=38
SENTIMENT_PANIC_THRESHOLD=72
SENTIMENT_LOW_COVERAGE_COUNT=5
SENTIMENT_FULL_COVERAGE_COUNT=30
```

当 `trade_permission=NO_BUY` 或恐慌分达到阈值时，系统禁止新增买入；当 `trade_permission=LIGHT_ONLY` 时，只允许更高分标的小仓试错。

## 静默自学习

自学习不修改程序代码，只更新 `storage/strategy_params.json` 里的受控参数。每次更新都会写入 SQLite 的 `learning_runs` 和 `strategy_param_versions` 表，方便追踪和回滚。

它不是每天强行优化，而是每天做一次体检：

- 如果表现稳定，保持参数不变
- 如果样本不足，保持参数不变
- 如果刚调过参数且仍在冷却期，保持参数不变
- 如果 TuShare 结果归因发现明显问题，再交给 MiniMax 复核
- MiniMax 不批准或置信度不足，保持参数不变
- 只有全部通过，才小幅更新白名单参数

默认工作日 20:30 执行：

```text
读取最近推荐 -> 查询推荐后日线表现 -> 计算胜率/收益/回撤/止损率 -> 生成参数建议 -> 安全边界校验 -> 小幅更新参数
```

可优化参数包括：

- `CONFIDENCE_THRESHOLD`
- `SENTIMENT_MIN_BUY_SCORE`
- `SENTIMENT_RISK_OFF_SCORE`
- `SENTIMENT_PANIC_THRESHOLD`
- `MIN_TURNOVER_YI`
- `MAX_RECOMMEND_PCT_CHANGE`
- `BUY_RANGE_PULLBACK_PCT`
- `MAX_CHASE_PCT`
- `STOP_LOSS_PCT`
- `MAX_PUSH_STOCKS`

安全规则：

- 样本不足不更新
- 单次参数调整有上限
- 不自动提高最大仓位
- 只在结果有明显方向时更新
- 参数更新才发飞书升级报告；无变化不刷屏
- 升级报告会说明改了哪些参数，以及提升了哪方面能力

默认配置：

```text
SELF_LEARNING_ENABLED=true
SELF_LEARNING_HOUR=20
SELF_LEARNING_MINUTE=30
SELF_LEARNING_LOOKBACK_DAYS=30
SELF_LEARNING_MIN_SAMPLES=30
SELF_LEARNING_MAX_STEP_PCT=0.08
SELF_LEARNING_APPLY_CHANGES=true
SELF_LEARNING_NOTIFY=true
SELF_LEARNING_COOLDOWN_DAYS=5
SELF_LEARNING_STABLE_MIN_WIN_RATE=0.55
SELF_LEARNING_STABLE_MIN_AVG_RETURN_PCT=0.2
SELF_LEARNING_STABLE_MAX_STOP_RATE=0.12
SELF_LEARNING_AI_REVIEW_ENABLED=true
SELF_LEARNING_AI_MIN_CONFIDENCE=0.65
```

## 盘中防轰炸

系统会记录上一条真正发出的盘中推荐指纹。同一批股票、同一整体信号、同一风险状态、同一买入区间和最高追价，不会每 5 分钟重复推送。

只有以下变化会重新推送：

- 推荐股票列表变化
- `BUY/HOLD/SELL` 整体信号变化
- 风险等级变化
- 市场状态变化
- 买入区间、最高追价或止损价变化超过 `PUSH_PRICE_CHANGE_THRESHOLD`
- 距离上次盘中推送超过 `PUSH_DEDUP_SUMMARY_MINUTES`，发送一条状态摘要

默认配置：

```text
PUSH_DEDUP_ENABLED=true
PUSH_DEDUP_SUMMARY_MINUTES=60
PUSH_PRICE_CHANGE_THRESHOLD=0.01
PUSH_NO_RECOMMENDATION=false
```

## 复盘文章

复盘文章功能默认关闭，不会自动生成，也不会推送到飞书。需要恢复时设置：

```text
REVIEW_ENABLED=true
REVIEW_HOUR=20
REVIEW_MINUTE=0
REVIEW_TARGET_CHARS=1000
```

手动运行一次盘中循环：

```bash
python -m app.main --once
```

手动生成一次复盘：

```bash
python -m app.main --review-once
```

当 `REVIEW_ENABLED=false` 时，手动命令只会返回 `review_disabled`，不会生成文章。
