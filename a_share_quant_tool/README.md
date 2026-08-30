# A-Share Quant Tool

个人主动量化研究工具：TuShare 数据逐日归档 → 无前视回测 → 模拟盘/影子交易 → 受控 miniQMT 下单。DeepSeek 只负责结构化研究报告，不参与下单。它不是收益承诺、荐股服务或代客理财系统。

## 当前交付

- `a_share_quant.data.TushareDataClient`：主数据源。不可覆盖 Parquet 存档含全市场日线、日频指标、涨跌停价、停复牌、股票主表、涨停/炸板/跌停池、最强概念、资金流和业绩预告；没有相应积分权限时显式失败。
- `a_share_quant.sentiment`：冰点、修复、主升、高潮、退潮五态和最大风险敞口。
- `a_share_quant.strategies`：首板、业绩超预期低位共振、20日新高回踩、龙虎榜延迟跟买四个可审计筛选器。
- `a_share_quant.backtest`：信号日收盘产生、下一交易日开盘成交；佣金、最低5元、卖出印花税、单边滑点、停牌、ST、涨跌停、T+1 和整手约束。
- `a_share_quant.portfolio` / `a_share_quant.exits`：组合最大敞口、单日新建仓位上限、单票上限、连续亏损降仓，以及首板的低开/冲高回落/止损/持有期退出纪律。
- `a_share_quant.paper`：独立纸面账户与不可重复的订单审计记录。
- `a_share_quant.qmt`：miniQMT `xtquant` 适配器，默认不可能提交订单；每笔委托都要求显式确认、数据新鲜、账户对账、限额和 kill switch 检查。
- `a_share_quant.deepseek`：只接收已归档、带交易日的情绪/信号/数据质量事实，使用 JSON 模式输出日报、风险和数据缺口；结果不能改变策略信号或触发订单。

## 安装与最小运行

```bash
cd "/Users/ericlin/Documents/GitHub/AI Products/a_share_quant_tool"
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
pytest -q
```

将 `.env.example` 复制为 `.env`。默认 `ASQ_TUSHARE_SOURCE=dashboard_proxy`，量化工具以只读方式复用 Dashboard 已验证可用的 TuShare 代理配置，不复制或输出 Token。若 Dashboard 使用非默认 `APP_SECRET`，仅通过 `ASQ_DASHBOARD_APP_SECRET` 环境变量提供同一值。需要直接调用官方 TuShare 时，显式改为 `ASQ_TUSHARE_SOURCE=direct` 并填写 `TUSHARE_TOKEN`。DeepSeek 仍填写 `DEEPSEEK_API_KEY`。密钥严禁写入代码、归档、报告和日志。

每次数据请求都会追加一条不含 Token 和请求参数的审计记录到 `reports/tushare_call_audit.jsonl`，字段包括 UTC 时间、数据源、接口、成功状态、耗时和返回行数。它可用于精确统计本工具后续的每日调用量。

以 `build_daily_pipeline(Settings.from_env())` 创建数据管道时会自动采用这个已审计的数据源。可先做一次单接口健康检查（原始日线响应会按日期归档）：

```bash
a-share-quant tushare-health --date 20260818
```

示例情绪指标文件：

```json
{
  "date": "20260818", "limit_up_count": 62, "limit_down_count": 3,
  "max_board_height": 4, "broken_board_rate": 0.22,
  "yesterday_limit_up_return": 0.018, "total_turnover": 1.4,
  "turnover_ma5": 1.2, "turnover_ma20": 1.0
}
```

```bash
a-share-quant sentiment --metrics metrics.json
```

## 运行顺序与边界

1. 在每个交易日收盘后以 `TushareDataClient` 运行 `DailyPipeline.refresh()`；原始响应一旦归档不可覆盖。
2. 计算 `SentimentMetrics`。`SentimentEngine` 的信号只对下一交易日有效。
3. 仅在数据完整、非冰点/退潮、且 `PortfolioRiskEngine` 放行时生成策略候选；首板还需要次日真实可成交性确认。
4. 使用已归档的点时点股票池、交易日历和公告时间运行回测；不能用当前成份股替代历史股票池。
5. 先跑纸面账户和至少数周影子交易，并做券商成交/持仓/现金逐日对账。
6. QMT 实盘需要手工设置环境开关，且每笔调用传入 `confirm_live_order=True`。没有“生成信号即下单”的代码路径。

## 回测验收

提交策略前必须保存：样本外/滚动窗口、牛熊震荡分段、2024 年初流动性收缩窗口、成本敏感性、阈值 ±20% 敏感性、IC/单调性（适用于横截面因子）和成交失败假设。高位一字板买入和跌停卖出均应视为未成交。

## QMT 上线前检查

- 使用仅交易所需的证券账户权限，不记录账户号、密码或 token 到仓库、日志或报告。
- 验证 `xtquant` 版本与券商 miniQMT 客户端一致，先完成连接、资产/持仓/当日订单/成交查询和回调订阅。
- 限制单票、单笔、日委托数、日亏损、总回撤；数据过期、对账失败或 kill switch 触发时阻断所有订单。
- 接口调用基于 miniQMT 对 `order_stock(account, stock_code, order_type, order_volume, price_type, price, strategy_name, order_remark)` 的公开说明；上线前仍须以券商提供的本机版本文档和模拟环境复核。

TuShare 的不同接口有不同积分/频率权限；`limit_list_ths`、最强概念等高价值情绪接口也有个人研究使用边界，应先在你的账号中验证权限和字段。DeepSeek 当前应使用 `deepseek-v4-flash` 或 `deepseek-v4-pro`，不用即将停止的旧模型名。[TuShare 日频指标](https://tushare.pro/document/2?doc_id=32)、[TuShare 涨停池](https://tushare.pro/document/2?doc_id=355)、[DeepSeek JSON 模式](https://api-docs.deepseek.com/guides/json_mode/)、[miniQMT 交易 API](https://www.miniqmt.com/pages/docs/xttrader.html)。
