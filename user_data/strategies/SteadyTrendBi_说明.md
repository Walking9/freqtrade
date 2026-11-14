# SteadyTrendBi 策略说明（BTC/ETH 双向版）

> 交付物：`freqtrade/user_data/strategies/SteadyTrendBi.py`（定稿，勿改参数）
> 回测配置模板：`freqtrade/user_data/config_backtest_bi.json`（含本地 testnet 补丁设置，**勿用于实盘**）
> 同族策略（仅多头 BTC/ETH/SOL）：`SteadyTrend.py`；研究日志：`/Users/luka/Project/freqtrade_src/AI_TRADING_WORKLOG.md`

---

## 一、策略规则

一句话：**BTC/ETH 各自的 4h 趋势向上时，1h 突破 4 天最高价做多；4h 趋势向下时，1h 跌破 4 天最低价做空；趋势转弱或亏损 8% 离场。**

- **Regime（4h 信息框架）**：多头环境 = `close_4h > EMA200_4h` 且 `EMA50_4h > EMA200_4h`；空头环境 = 反向。
- **入场（1h）**：多头环境内收盘突破过去 96 根最高价 → 做多；空头环境内收盘跌破过去 96 根最低价 → 做空。
- **出场**：4h 收盘跌破（多头）/升破（空头）`EMA50_4h` → 离场；或触发 -8% 硬止损。
- **仓位**：1x 杠杆、市价单、仅 BTC/ETH 永续；`max_open_trades=3` 时每笔约 1/3 资金（两对最多同时 2 笔）。
- **设计纪律**：与 SteadyTrend 同源骨架，多空参数完全对称，未对做空侧单独调参（无寻优、无单边特调）。

## 二、回测结果（诚实口径）

口径：Binance USDT 永续、1000 USDT 起始、`max_open_trades=3`、已含 0.05% 双边手续费与真实历史资金费率、未建模滑点；
数据截至 2026-11-10（含最近 BTC 跌破 7.4 万的下跌段）。交易对元数据经官方 testnet 镜像（对 1h 级回测影响可忽略）。

### 双向版分段表现

| 分段 | 收益 | 最大回撤 | PF | 笔数 | 多头/空头拆分 |
|---|---|---|---|---|---|
| 开发窗 2020-02 → 2025-06 | +110.8% | 32.6% | 1.21 | 328 | 多 +161.8% / 空 **-51.1%** |
| 留出集 2025-07 → 2026-11 | **+9.2%** | 18.0% | 1.15 | 85 | 多 +7.4% / 空 **+1.8%** |
| 全周期 2020-02 → 2026-11 | +129.6%（CAGR 13.1%） | 32.6% | 1.19 | 413 | 多 +177.1% / 空 -47.5% |
| 2022 熊市单年 | -7.4% | 25.4% | 0.89 | 64 | 多 -11.0% / 空 +3.5% |

全周期最大回撤 32.6% 发生在 **2022-07 → 2023-10**（熊市磨底 + 两边被打脸的震荡段）。

### 与仅多头（同 BTC/ETH、同仓位口径）对照

| 口径 | 双向 Bi | 仅多头 SteadyTrend |
|---|---|---|
| 全周期 | +129.6%，CAGR 13.1%，DD 32.6%，PF 1.19 | +190.1%，CAGR 17.1%，DD 19.3%，PF 1.52 |
| 最近 16 个月（2025-07→） | +9.2%，DD 18.0% | +6.7%，DD 13.8% |

参考：加上 SOL 的三币仅多头版全周期 +756%（SOL 贡献最大，去掉 SOL 后收益中枢必然下移）。

### 怎么解读（重要）

- 做空侧本质是**"崩盘保险"**：下跌段里赚钱（2022 空头 +3.5%、最近 16 个月空头 +1.8%），但全周期净贡献 -47.5 个百分点，
  且把最大回撤从 19.3% 抬到 32.6%。它的价值在"下跌市也能有收益来源"，代价是"长期收益更低、震荡期回撤更深"。
- 双向版在**震荡市会被两头打**（2022 下半年那 32.6% 的回撤就是这么来的），这是做空的固有成本，不是 bug。
- 如果你接受"收益中枢下移 + 回撤加深"换取"不预测方向的对称参与"，这个版本就是为此设计的；否则仅多头版更优。

## 三、当前市场状态（本地数据截至 2026-11-10 23:00 UTC）

| 币对 | 最新价 | 4h Regime | 做空触发位 | 做多触发位（还需 regime 转多） |
|---|---|---|---|---|
| BTC | 73,763 | **SHORT（只允许做空）** | 跌破 72,691（-1.5%） | 突破 76,210 |
| ETH | 2,052 | **SHORT（只允许做空）** | 跌破 2,004（-2.4%） | 突破 2,251 |

即：当前两个币都处于空头环境，**再跌 1.5%~2.4% 就会触发空头入场**；反弹则不开新仓（多单需要 4h 趋势整体修复，通常需要数周）。

## 四、VPS 部署（与现有机器人并存）

freqtrade 一个进程只能跑一个策略，需**另一个实例**：

1. 上传 `SteadyTrendBi.py` 到 `user_data/strategies/`。
2. 新建 `user_data/config_bi.json`（以你现有 config 为底，重点改这几处）：

```json
{
    "max_open_trades": 3,
    "trading_mode": "futures",
    "margin_mode": "isolated",
    "dry_run": true,
    "dry_run_wallet": 1000,
    "entry_pricing": { "price_side": "other", "use_order_book": true, "order_book_top": 1 },
    "exit_pricing":  { "price_side": "other", "use_order_book": true, "order_book_top": 1 },
    "exchange": {
        "name": "binance",
        "pair_whitelist": ["BTC/USDT:USDT", "ETH/USDT:USDT"]
    },
    "db_url": "sqlite:///trades_bi.sqlite",
    "api_server": { "enabled": true, "listen_port": 8081 },
    "strategy": "SteadyTrendBi"
}
```

3. 启动：
```bash
freqtrade trade --config user_data/config_bi.json --strategy SteadyTrendBi \
    --log-file user_data/logs/bi.log
```

要点：
- **与现有机器人并存必须改**：`db_url`（否则共库冲突）、`api_server.listen_port`（8080 被占用）、日志文件。
- VPS 上**不需要**本地的 testnet 补丁（那只解决本机 451 封锁；VPS 能正常访问 Binance 就行，若 VPS 也被 451 则需要换线路）。
- 对比实验建议：两个机器人用相同 `dry_run_wallet`；至少并行跑 1~2 个月，按月记录收益/回撤/交易数。
- `max_open_trades` 想更激进可改 2（每笔 1/2 资金），对应回撤近似等比放大，自行权衡。

## 五、注意事项

1. **不要上杠杆**（策略固定 1x）。杠杆会等比放大 32.6% 级别的回撤，可能直接爆仓。
2. **空头会被轧空**：加密市场急涨（squeeze）时空头 -8% 止损被扫是正常成本；空头胜率与多头一样只有约 1/3。
3. **不预测方向**：如果"年底大行情"是持续多日的单边（无论上下），策略会在早期上车；如果是单日 V 形急涨急跌，
   大概率吃不到甚至两头小亏——这是趋势策略共性，不要因此手动干预。
4. **资金费率**：恐慌下跌中资金费率常转负（空头付费），回测已按历史真实费率计入。
5. **不要调参**：多空参数与验证过的骨架同源，任何"优化空头参数"都会引入过拟合。
6. **对照使用**：建议先与仅多头版 SteadyTrend 并行模拟盘对比，再决定实盘资金分配；历史证据表明"空头的价值高度依赖行情"。
7. 风险提示：历史回测不代表未来收益，请只用可承受损失的资金。

## 六、文件清单

| 文件 | 说明 |
|---|---|
| `strategies/SteadyTrendBi.py` | 双向策略本体（定稿） |
| `user_data/config_backtest_bi.json` | 回测配置（本地用，勿直接上 VPS 实盘） |
| `ai_work/logs/bi_summary.txt` + `bi_*.log` | 本次全部回测原始日志 |
| `ai_work/scripts/bi_pipeline.sh` | 一键复跑脚本（数据更新+6 组回测+信号快照） |
| `ai_work/scripts/current_signal.py` | 当前 regime / 多空触发位快照工具 |
