# Q-GARP v4 style-recognition audit (research only)

Date: 2026-08-16  
Scope: Q-GARP isolated point-in-time cache; no hybrid_alpha, ETF, Dashboard or execution integration.

## Safety state

`QGARP_PAPER_ENABLED=false`, `QGARP_PUSH_ENABLED=false`, and `QGARP_DRY_RUN=true` were set for every run. The local Q-GARP runner and config are additionally research-locked: `--paper-once` returns `disabled_research_only` and cannot create a paper order.

## Backtest contract

- Calendar-year, independent tests: 2019--2026 (2026 through 2026-07-31).
- Point-in-time daily-basic membership and financial availability gate.
- Adjusted-price coverage check, next-open execution, T+1, tradability checks, commission, stamp duty, and slippage.
- Benchmark: `000906.SH`.
- Turnover is gross annual turnover divided by initial capital (`turnover_multiple`).

## Diagnosis of the prior v4

The deployed source and the saved optimizer report identify the prior code as `v4.0` with `v4.1` parameter variants, not a v4.3 style model. It ranks quality/value/low-vol stocks (growth and momentum weights are zero) then applies a generic index/breadth gate. Its optional industry filter is based on the same defensive candidate list. This explains the defensive 2020--2023 relative results and the persistent failure to participate in offensive regimes: it has no direct stock leadership or style-rotation signal.

The strongest saved prior variant, `v4.1_attack_balanced`, was already rejected: four positive-excess years out of eight, average annual excess **-4.39%**, and worst annual drawdown **-18.55%**.

| Year | Excess return | Max drawdown | Turnover |
|---|---:|---:|---:|
| 2019 | -29.31% | -11.59% | 6.53x |
| 2020 | +7.08% | -14.38% | 9.76x |
| 2021 | +3.73% | -8.36% | 6.21x |
| 2022 | +15.09% | -8.74% | 2.24x |
| 2023 | +11.21% | -6.35% | 4.50x |
| 2024 | -21.80% | -11.56% | 4.75x |
| 2025 | -14.30% | -6.87% | 5.90x |
| 2026* | -6.78% | -18.55% | 5.39x |

## Rebuild round 1: v4.4 breakout leadership — rejected

Hypothesis: use point-in-time fundamentals only for eligibility, then select risk-on stocks by stock/index relative strength, leading industry strength, 20/60-day trend alignment, near-60-day-high breakout and volume persistence. This was a true offensive-layer rebuild, not a quality/low-vol weight change.

| Year | Excess return | Max drawdown | Turnover |
|---|---:|---:|---:|
| 2019 | -38.02% | -17.92% | 7.01x |
| 2020 | -21.47% | -14.67% | 7.53x |
| 2021 | -7.00% | -19.97% | 6.18x |
| 2022 | +8.79% | -15.30% | 3.34x |
| 2023 | +5.07% | -13.57% | 5.08x |
| 2024 | -33.75% | -23.75% | 5.40x |
| 2025 | -20.99% | -17.06% | 9.66x |
| 2026* | -27.64% | -34.08% | 3.86x |

Verdict: 2/8 positive-excess years, average excess **-16.88%**, worst drawdown **-34.08%**. The breakout/volume condition chased fragile momentum rather than identifying durable leaders.

## Rebuild round 2: v4.5 style rotation — rejected

Hypothesis: remove the near-high chase; require sustained 60-day relative strength and leading industry strength, but buy a controlled pullback above the 60-day trend with non-collapsing volume.

| Year | Excess return | Max drawdown | Turnover |
|---|---:|---:|---:|
| 2019 | -21.31% | -16.51% | 7.13x |
| 2020 | -29.72% | -19.07% | 6.01x |
| 2021 | -17.45% | -22.76% | 5.69x |
| 2022 | +4.98% | -18.46% | 3.23x |
| 2023 | -1.05% | -15.37% | 4.59x |
| 2024 | -38.00% | -26.01% | 5.09x |
| 2025 | -22.26% | -17.89% | 8.90x |
| 2026* | -23.10% | -31.27% | 3.86x |

Verdict: 1/8 positive-excess years, average excess **-18.49%**, worst drawdown **-31.27%**. Broadening the entry to controlled pullbacks did not fix the cross-regime failure.

## 因子与动能研究：v4.6 因子动量 — 拒绝

先对 2019--2026、90 个可用月度截面执行 20 日前瞻诊断。质量、价值和低波具有正向平均 Rank-IC / 分层收益；原始中期动量则为负，不能直接作为追涨选股因子。

| 因子 | 平均 Rank-IC | 正 IC 月份比例 | 高低分组 20 日收益差 |
|---|---:|---:|---:|
| 质量 | +0.0261 | 62.22% | +0.91% |
| 成长 | +0.0077 | 53.33% | +0.14% |
| 价值 | +0.0355 | 58.89% | +1.47% |
| 中期动量 | -0.0123 | 50.00% | -0.76% |
| 低波 | +0.0516 | 57.78% | +1.82% |

v4.6 在每个调仓日用可得截面数据计算五个因子的高低组合近 20/60 日收益扩散，并据此动态分配因子权重；个股端加入相对基准的 60 日动量与 5 日过热过滤。它明显改善了 v4.4/v4.5 的回撤，但没有实现跨牛熊年份的稳健超额。

| Year | Excess return | Max drawdown | Turnover |
|---|---:|---:|---:|
| 2019 | -33.63% | -9.17% | 4.56x |
| 2020 | -9.43% | -12.76% | 5.18x |
| 2021 | -2.33% | -12.27% | 4.81x |
| 2022 | +13.14% | -9.88% | 1.98x |
| 2023 | +4.10% | -11.06% | 3.56x |
| 2024 | -13.43% | -7.92% | 3.87x |
| 2025 | +2.63% | -6.43% | 4.68x |
| 2026* | -5.86% | -10.84% | 1.12x |

Verdict: 3/8 positive-excess years, average excess **-5.60%**, worst drawdown **-12.76%**. It is materially safer than the style-chasing versions, but still fails the offensive 2019/2020/2024 and 2026 tests.

## 因子与动能研究：v4.7 条件动量/反转 — 拒绝

v4.7 不使用事后固定的“动量反向”。每次调仓时，它只检查**当时已可观察**的动量因子高低组合收益差：差值为正时采用顺势模式，为负时采用受限的相对反转模式；其他因子状态权重、点时点股票池、成本与执行假设保持不变。

| Year | Excess return | Max drawdown | Turnover |
|---|---:|---:|---:|
| 2019 | -33.63% | -9.17% | 4.56x |
| 2020 | -9.43% | -12.76% | 5.18x |
| 2021 | -2.33% | -12.27% | 4.81x |
| 2022 | +13.14% | -9.88% | 1.98x |
| 2023 | +4.10% | -11.06% | 3.56x |
| 2024 | -14.17% | -7.92% | 4.12x |
| 2025 | +2.63% | -6.43% | 4.68x |
| 2026* | -5.86% | -10.84% | 1.12x |

Verdict: 3/8 positive-excess years, average excess **-5.70%**, worst drawdown **-12.76%**. Conditional reversal did not improve the prior factor-momentum result; it is rejected.

## 故障归因：为什么因子/动能版本没有产生稳健超额

对 v4.7 执行 2019-01 至 2026-07 的只读逐月计划归因（91 次调仓）后，问题可以分成四层：

1. **仓位门控压制了进攻参与。** 91 个月中仅 7 个月为 `offensive`，73 个月为 `transition`，11 个月为 `cash_defense`；平均目标仓位仅 26.99%，平均只选 5.74 只股票。2019/2020/2025 等上涨年份并未真正参与市场。更严重的是，组合分配被 `min(exposure, selected_count × 8%)` 再次上限约束；即便出现进攻状态，选中 5--7 只股票也只能配置 40--56%，达不到声明的 90% 进攻仓位。

2. **当前“因子动量”定义对动量因子存在机械相关。** v4.7 在日期 *t* 用日期 *t* 的因子排名，回看同一批股票在 *t-20* 至 *t*、*t-60* 至 *t* 的收益。中期动量排名本身由过去价格收益构成，因此“高动量组过去收益更高”几乎是定义重述，而不是可交易的因子组合收益。结果是动量被列为因子领先者 89/91 个月，顺势模式也触发 89/91 个月；这与独立的前瞻 20 日诊断（动量 Rank-IC -0.0123、分层收益 -0.76%）矛盾。v4.7 的反转开关因此几乎没有机会发挥作用。

3. **质量/价值因子的历史收益差也不是当时可交易的因子组合 NAV。** 在 *t* 才可得的财务排名被用于解释 *t-60* 至 *t* 的价格收益；这并不直接使用未来价格，但该排名在回看窗口开始时尚不存在，不能作为因子状态收益。正确的做法是在月末 *t-1* 冻结分组，并仅在 *t* 以后记录该组合的已实现收益。

4. **评价指标混合了选股与仓位择时。** 当前直接以满仓中证 800 计算年超额；这对现金防守组合是必要的总收益约束，但无法回答“选股是否创造 alpha”。例如 v4.7 在 2020 和 2025 的实际收益分别为 +12.30%、+27.60%，平均仓位仅约 31%、30%，存在仓位折算后正选股贡献的迹象；而 2021、2022、2023、2026 即使按实际仓位折算仍显著落后，说明问题并非只有低仓位。

### 经验证的逐月归因

| 指标 | 结果 |
|---|---:|
| 调仓月份 | 91 |
| 进攻 / 过渡 / 现金月份 | 7 / 73 / 11 |
| 平均目标仓位 | 26.99% |
| 平均入选数 | 5.74 |
| 平均可选股票数 | 462.91 |
| 动量顺势 / 反转月份 | 89 / 2 |
| 平均正向因子数 | 2.08 / 5 |

## 纠正后的研究设计（尚未提升为候选策略）

下一版不能直接调阈值，应先重建真实、无泄漏的因子状态账本：

1. 每个历史月末冻结当时可得的因子五分位组合；从下月开始记录其净成本、可交易收益。
2. 在月末只使用已经完成的前 1/3/6 个月因子组合 NAV 来判断因子动量，绝不能用当前排名回看当前收益。
3. 把报告拆成：满仓基准超额、实际仓位折算的选股 alpha、平均仓位、状态覆盖率和换手；五项必须同时通过。
4. 在因子状态有效性明确前，不调整进攻仓位阈值、不开放纸面执行，也不把结果接入其他策略。

## 重建验证：v4.8 冻结因子状态 — 结构问题已修复，经济验证仍失败

v4.8 implements the corrected design above:

- Frozen factor quintiles are formed at prior month-ends. A state at date *t* may read only portfolios formed before *t*; a unit test explicitly rejects same-date portfolio use.
- Factor state is a stock-ranking weight, not an `offensive` hard gate. Market trend and breadth alone determine exposure state.
- Transition exposure is 50%, and selection uses hard-risk fallbacks to fill up to 14 names. This removes the accidental `selected_count × 8%` cash cap.
- Backtest now reports the full-benchmark excess **and** an exposure-adjusted benchmark / selection alpha.

| Year | Total return | Full benchmark excess | Exposure-adjusted benchmark | Selection alpha | Max drawdown | Turnover | Average exposure |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2019 | -0.90% | -35.76% | +9.56% | -10.46% | -16.32% | 10.23x | 49.29% |
| 2020 | +17.93% | -3.79% | +15.10% | +2.83% | -15.65% | 10.40x | 53.17% |
| 2021 | +1.65% | +4.07% | -4.24% | +5.89% | -12.44% | 9.46x | 48.07% |
| 2022 | -18.37% | +2.91% | -9.52% | -8.86% | -18.49% | 4.96x | 29.28% |
| 2023 | -15.61% | -4.09% | -9.44% | -6.17% | -18.86% | 8.30x | 49.25% |
| 2024 | +8.22% | -7.43% | +7.57% | +0.65% | -9.49% | 8.84x | 41.83% |
| 2025 | +24.74% | -0.23% | +17.48% | +7.27% | -12.95% | 11.89x | 57.70% |
| 2026* | -28.02% | -24.25% | -7.77% | -20.24% | -31.16% | 3.65x | 34.68% |

The rebuild raises average participation from v4.7's 26.99% planned exposure to 45.34% realised exposure and provides genuine attribution. However, selection alpha is negative in 2019, 2022, 2023 and 2026; annual turnover also reaches 8--12x in several years. Therefore the implementation fixes are accepted, but **v4.8 is rejected as an investment candidate**: it has only 2/8 positive full-benchmark excess years, average full-benchmark excess -8.57%, and worst drawdown -31.16%.

## Research conclusion

No v4 candidate is robust. A candidate may be called robust only after positive (or otherwise pre-specified acceptable) net excess return, tolerable drawdown and practical turnover in each examined offensive, defensive, range and risk-off regime. Neither rebuilt candidate satisfies that test; neither should be promoted, paper traded, pushed or connected to any production strategy.

The next defensible research step is data diagnosis before another factor change: calculate monthly, rolling rank-IC, decile spreads and return decay separately for factor spreads, stock relative strength, industry relative strength, breakout and pullback features, with complete point-in-time industry classifications. Do not continue threshold searching until at least one signal has stable sign in a calibration period **and** an untouched holdout regime.

## 进行中：v4.9 行业中性动量诊断（尚未进入组合回测）

假设不是继续改变 v4.8 的质量/低波权重，而是把价格信号拆为三项可独立证伪的预测量：行业 60 日相对强度、行业内 60 日残差动量，以及 5 日短期反转；组合信号为后两者的预先固定加权（70%/30%）。诊断器在每个月末仅用当期 PIT 股票池、当期复权价格和当期可得财报形成信号，以其后 20 个交易日的相对基准收益进行评估。

准入规则已经预先锁定：训练期的入选信号必须同时具有正的平均 Rank-IC 与正的十分位多空超额收益，且 2026 留出期的组合信号仍为正；否则直接拒绝，不进行参数搜索或年度组合回测。该模块不会创建订单、推送或写入生产策略。远端 PIT 缓存诊断正在等待研究服务器重新可达后读取结果。

This is research evidence, not a return guarantee or personalized investment advice.
