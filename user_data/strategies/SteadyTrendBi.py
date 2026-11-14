"""SteadyTrendBi — BTC/ETH 永续双向趋势跟踪策略(1h 框架 + 4h regime)。

SteadyTrend(仅多头、BTC/ETH/SOL)的对称双向版:
- 多头: 4h regime 向上(close_4h > EMA200_4h 且 EMA50_4h > EMA200_4h)时,
  1h 收盘突破过去 96 根(4 天)最高价 → 做多。
- 空头: 4h regime 向下(close_4h < EMA200_4h 且 EMA50_4h < EMA200_4h)时,
  1h 收盘跌破过去 96 根最低价 → 做空。
- 出场: 4h 收盘穿越 EMA50_4h(多头跌破 / 空头升破)→ 离场; 或 -8% 硬止损。
- 仅 BTC/ETH、1x 杠杆、市价单、无 ROI 止盈、无移动止损。

设计纪律:
- 多空完全对称, 参数与 SteadyTrend 同源(唐奇安 96 / -8% 止损 / EMA50 快出场), 未对做空侧单独调参。
- 空头侧在 2020-2025(牛市为主)区间是净成本的"保险费", 其价值体现在下跌段(2022、2025-26);
  完整口径的分段结果见 SteadyTrendBi_说明.md, 请以全周期口径评估, 不要只看单边。

风险提示(做空特有):
- 加密市场轧空(squeeze)剧烈, 空头 -8% 止损被扫属正常交易成本; 双向版回撤预期高于仅多头版。
- 恐慌下跌中资金费率常转负(空头付费); 回测已计入历史真实资金费率。
- 本策略不做方向预测: 大的单边行情向上(突破做多)或向下(跌破做空)都会尝试参与,
  但需要行情"持续多日"才有利润; 单日 V 形急涨急跌大概率吃不到、甚至两头小亏(趋势策略共性)。
- 市价单要求配置 `entry_pricing/exit_pricing` 的 `price_side = "other"`; 需 futures 模式。
"""

import talib.abstract as ta
from pandas import DataFrame

from freqtrade.strategy import IStrategy, informative


class SteadyTrendBi(IStrategy):
    INTERFACE_VERSION = 3

    # 基础框架 1h; 4h 作为 regime 信息框架
    timeframe = "1h"
    can_short = True
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
        # 唐奇安通道 (96 根 1h = 4 天); shift(1) 只用已收盘历史, 防自引用
        dataframe["dc_high"] = dataframe["high"].rolling(96, min_periods=96).max().shift(1)
        dataframe["dc_low"] = dataframe["low"].rolling(96, min_periods=96).min().shift(1)
        return dataframe

    # ------------------------- 入场 -------------------------
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = 0
        dataframe["enter_short"] = 0
        dataframe["enter_tag"] = ""

        regime_long = (dataframe["close_4h"] > dataframe["ema200_4h"]) & (
            dataframe["ema50_4h"] > dataframe["ema200_4h"]
        )
        regime_short = (dataframe["close_4h"] < dataframe["ema200_4h"]) & (
            dataframe["ema50_4h"] < dataframe["ema200_4h"]
        )

        long_cond = (
            (dataframe["volume"] > 0) & regime_long & (dataframe["close"] > dataframe["dc_high"])
        )
        short_cond = (
            (dataframe["volume"] > 0) & regime_short & (dataframe["close"] < dataframe["dc_low"])
        )

        dataframe.loc[long_cond, ["enter_long", "enter_tag"]] = (1, "breakout_long")
        dataframe.loc[short_cond, ["enter_short", "enter_tag"]] = (1, "breakout_short")
        return dataframe

    # ------------------------- 出场 -------------------------
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        dataframe["exit_tag"] = ""

        # 4h 收盘穿越 EMA50_4h → 趋势转弱, 离场
        exit_long = dataframe["close_4h"] < dataframe["ema50_4h"]
        exit_short = dataframe["close_4h"] > dataframe["ema50_4h"]

        dataframe.loc[exit_long & (dataframe["volume"] > 0), ["exit_long", "exit_tag"]] = (
            1,
            "regime_off",
        )
        dataframe.loc[exit_short & (dataframe["volume"] > 0), ["exit_short", "exit_tag"]] = (
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
