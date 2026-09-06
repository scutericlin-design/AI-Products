# 股票自动化研究改造

## 目的与边界

本模块借鉴公开的机构研究方法，建立可复现、可排错的股票多因子研究链路，不宣称取得任何私募的专有模型，也不保证盈利。

独立运行，不导入ETF、热点龙头或QGARP的账户、调度、交易模块。数据只写到显式指定的研究目录；研究命令不发送飞书、不产生模拟或实盘订单、不修改主策略参数。

## 机构公开方法对照

| 机构 | 可核实的公开方法 | 本系统可落地的借鉴 | 不能据此声称 |
|---|---|---|---|
| AQR | 质量因子、价值与动量组合、实际交易成本研究 | 将财务质量、估值、趋势分开评分；检验扣成本收益 | 这些论文就是AQR在A股的在用产品，更不意味着保证收益 |
| Two Sigma | AI研究流程、明确目标函数、人工监督、系统集成 | AI辅助解释和提出可验证假设；规则与数据审计决定是否执行 | 更换聊天模型就能复制Two Sigma的收益预测能力 |
| 幻方量化 | 多源数据、神经网络定价、系统化交易及研究基础设施 | 先修数据和验证链路，再讨论预测模型 | 已获得幻方的特征库、训练权重、交易代码或高频执行能力 |
| 九坤投资 | 多种产品目标：指增、对冲、多空、分散股票优选 | 明确区分超额收益、绝对收益与回撤目标，构建分散组合 | 九坤所有产品都靠择时躲熊市；官网明确指增不作仓位择时 |

来源：[AQR质量研究](https://www.aqr.com/Insights/Research/Working-Paper/Quality-Minus-Junk)、[AQR价值与动量](https://www.aqr.com/Insights/Datasets/Value-and-Momentum-Everywhere-Factors-Monthly?aqrPDF=1)、[AQR交易成本](https://www.aqr.com/Insights/Research/Working-Paper/Trading-Costs)、[Two Sigma 2026 AI观点](https://www.twosigma.com/articles/ai-in-investment-management-2026-outlook-part-i/)、[幻方量化官方介绍](https://www.high-flyer.cn/fund/)、[九坤官方策略介绍](https://www.ubiquant.com/website/about)。以上是公开依据，不是机构业绩排名或商业背书。

## 新策略的具体机制

1. 先用历史截面的流通市值和有效成交筛选研究股票池，不能把今天的热门股票倒填到2018年。
2. 财务数据按公告日期延后使用，指标缺失或过时要记录并排除，不能默认为质地好。
3. 分别计算质量、估值、中期动量、低波动分数，使用横截面排名合成；不把当日大涨当作公司质量。
4. 按风险预算分配目标仓位，控制单票和总仓位，加入历史收益相关性约束。没有历史行业数据时，不声称行业中性。
5. 使用每5个交易日再平衡与排名保留区间，避免每两分钟重新发明一次投资逻辑。实时行情检查和交易风控频率与选股频率是两件事。
6. `balanced`固定风险预算；`trend_scaled`通过平滑市场宽度和波动缩放预算。两个方案预先固定，比较结果不好也不自动反复搜索过去的最优参数。
7. 在下一交易日开盘做目标权重差额回放。真实股数撮合、涨跌停和公司行动账本尚未具备时，严格标注“复权单位研究代理”，不冒充可成交收益。
8. AI不能发明财务数字、扩大仓位或修改止损。现有主策略模型顺序与降级机制保持原样；新模块先使用确定性因子，不把非可复现的聊天输出混入历史回测。

`12只 / 单票8% / 总仓80%`是研究配置，不是机构秘密参数，也不是已经验证过的最优值。退出缓冲区与相似股票约束可以降低无意义轮换，但也可能延迟卖出或错过集中行情，必须实测。

## 如何运行

使用现有Python依赖，不需要新增交易服务。密钥只从环境读取；不要写入源文件。

```bash
python -m stock_alpha.data --directory /research/stock_alpha/2018_2026 --start 20180101 --end 20260904 --universe-limit 160
python -m stock_alpha.cli --data /research/stock_alpha/2018_2026 --output /research/stock_alpha/results --fetch-benchmark
python -m unittest tests.test_stock_alpha
```

下载逐请求原样缓存，可断点重跑，输入文件具有SHA-256校验。`--offline`只读取已有缓存；不会自动切换供应商或生成假数据。端点协议及字段见[TuShare财务指标](https://tushare.pro/document/2?doc_id=79)、[每日指标](https://tushare.pro/document/2?doc_id=32)。回放按日期区分2023年8月28日前后的卖出印花税，政策依据为[税务总局公告](https://fgk.chinatax.gov.cn/zcfgk/c102416/c5211343/content.html)；佣金与滑点仍是可配置研究假设。

## 独立AI配置

新策略首选模型为`deepseek-v4-flash`，Base URL为`https://tbtk.asia/v1`。配置通过本模块的`local_data/ai_config.json`读取（字段为`model`、`base_url`、`api_key`），该目录不纳入Git，含密钥的文件权限应为`600`。

可用`STOCK_ALPHA_AI_MODEL`、`STOCK_ALPHA_AI_BASE_URL`、`STOCK_ALPHA_AI_API_KEY`单独覆盖，或用`STOCK_ALPHA_AI_CONFIG`指定配置文件。不读取主策略的`STOCK_AI_*`或`MINIMAX_*`配置，不修改任何其他策略。

```bash
python -m stock_alpha.ai_config
python -m stock_alpha.ai_config --check
```

第一个命令只展示脱敏配置；第二个仅发送一条简短的连通性测试，不发送账户信息，不下单，不启动模拟盘。现有研究回测仍为确定性规则。独立实时模拟执行、AI事件评估和备用模型链由新增的 `stock_alpha.live` 接入，详见 [独立模拟盘说明](LIVE.md)。

## 升级验收

输出净收益、年度回撤、换手、成本翻倍测试、未成交和缺失数据诊断；比较沪深300价格指数及80%指数+20%现金的参考组合。价格指数不是含分红的可交易产品，也不是旧主策略的替身。

不能仅凭累计收益最高自动上线。必须进一步满足：历史状态和公司行动数据完整、同输入同决策的真实股数执行验证、与修正后旧策略同口径比较、独立时间段及成本压力下仍有优势、冻结版本后的前瞻模拟验证。已经多次看过的2018至2026历史，不再称为真正未见样本。

因此本模块输出始终包含`promoted=false`；当前没有通向生产交易的自动切换入口。研究完成不等于盈利目标已实现。
