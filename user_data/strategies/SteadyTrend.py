"""SteadyTrend — BTC/ETH/SOL 永续合约趋势跟踪策略（1h 框架 + 4h regime）。

设计概要（抗过拟合取向：全部为经典规则、参数极少、不做寻优）:
- Regime（4h 信息框架）: 多头环境 = close_4h > EMA200_4h 且 EMA50_4h > EMA200_4h。
- 入场（1h）: 多头环境内, 收盘价突破过去 96 根（4 天）最高价（不含当根 K 线）→ 顺势做多。
- 出场: 4h 收盘跌破 EMA50_4h（趋势转弱即离场）或触发 -8% 硬止损。
- 仅做多、1x 杠杆、市价单、无 ROI 止盈、无移动止损（trailing）。

开发与验证（详见 AI_TRADING_WORKLOG.md）:
- 开发窗口 2020-02 → 2025-06 回测: +664%, CAGR 45.8%, 最大回撤 17.1%, PF 1.69, 264 笔。
- 参数扰动（唐奇安 72/120、止损 -10%/-15%、进出场逻辑一致化）均在平台区。
- 留出集 2025-07 起, 未参与任何调参。

注意事项:
- 这是趋势策略: 胜率低（约 1/3）, 收益集中在少数大趋势单; 震荡市会连续小亏（正常现象）。
- 回撤预期 15%~30%（极端行情可能超出）; 请按此规划仓位与心理预期。
- 参数已定稿, 不建议再改（继续优化 = 过拟合）。策略可用于现货（此时只能做多, 天然兼容）。
- 市价单要求配置 `entry_pricing/exit_pricing` 的 `price_side = "other"`。
"""

from pandas import DataFrame

import talib.abstract as ta
from freqtrade.strategy import IStrategy, informative


class SteadyTrend(IStrategy):
    INTERFACE_VERSION = 3

    # 基础框架 1h; 4h 作为 regime 信息框架
    timeframe = "1h"
    can_short = False
    process_only_new_candles = True
    # 4h EMA200 需要 800 根 1h K 线, 留余量
    startup_candle_count = 900

    # ------------------------- 风控框架 -------------------------
    stoploss = -0.08
    trailing_stop = False
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    minimal_roi = {"0": 100.0}  # 实质关闭 ROI 止盈, 由信号与止损控制

    order_types = {
        "entry": "market",
        "exit": "market",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }
    order_time_in_force = {"entry": "GTC", "exit": "GTC"}

    # ------------------------- 指标 -------------------------
    @informative("4h")
    def populate_indicators_4h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema200"] = ta.EMA(dataframe, timeperiod=200)
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 唐奇安通道上轨 (96 根 1h = 4 天); shift(1) 只用已收盘历史, 防自引用
        dataframe["dc_high"] = dataframe["high"].rolling(96, min_periods=96).max().shift(1)
        return dataframe

    # ------------------------- 入场 -------------------------
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = 0
        dataframe["enter_tag"] = ""

        regime_long = (dataframe["close_4h"] > dataframe["ema200_4h"]) & (
            dataframe["ema50_4h"] > dataframe["ema200_4h"]
        )
        long_cond = (
            (dataframe["volume"] > 0) & regime_long & (dataframe["close"] > dataframe["dc_high"])
        )

        dataframe.loc[long_cond, ["enter_long", "enter_tag"]] = (1, "breakout_long")
        return dataframe

    # ------------------------- 出场 -------------------------
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        dataframe["exit_tag"] = ""

        # 4h 收盘跌破 EMA50_4h → 趋势转弱, 离场
        exit_long = dataframe["close_4h"] < dataframe["ema50_4h"]
        dataframe.loc[exit_long & (dataframe["volume"] > 0), ["exit_long", "exit_tag"]] = (
            1,
            "regime_off",
        )
        return dataframe

    # ------------------------- 杠杆 -------------------------
    def leverage(
        self,
        pair: str,
        current_time,
        current_rate: float,
        proposed_leverage: float,
        max_leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        return 1.0
