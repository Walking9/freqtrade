"""BTC/ETH 4h volatility expansion within a medium-term upward trend.

Fixed rules: BB(20,2) closing breakout + EMA72 > EMA216; middle-band exit;
10% exchange hard stop. 1x only. Entry capital fraction min(49.5%, 30% /
trailing 30-day annualized realized volatility). Cash is intentional outside
qualified trends. No hyperopt, DCA, pair-specific settings, or future data.
"""

from math import isfinite

import numpy as np
import talib.abstract as ta
from pandas import DataFrame

from freqtrade.strategy import IStrategy


class ExpansionTrend(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "4h"
    startup_candle_count = 1200
    process_only_new_candles = True
    can_short = False
    position_adjustment_enable = False
    minimal_roi = {}
    stoploss = -0.10
    trailing_stop = False
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    order_types = {
        "entry": "market",
        "exit": "market",
        "stoploss": "market",
        "stoploss_on_exchange": True,
        "stoploss_on_exchange_interval": 60,
    }
    order_time_in_force = {"entry": "GTC", "exit": "GTC"}

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["annvol"] = np.log(dataframe["close"]).diff().rolling(
            180, min_periods=180
        ).std() * np.sqrt(365 * 6)
        dataframe["ema72"] = ta.EMA(dataframe, timeperiod=72)
        dataframe["ema216"] = ta.EMA(dataframe, timeperiod=216)
        bands = ta.BBANDS(dataframe, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
        dataframe["bbupper"] = bands["upperband"]
        dataframe["bbmid"] = bands["middleband"]
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = (
            (dataframe["volume"] > 0)
            & (dataframe["close"] > dataframe["bbupper"])
            & (dataframe["ema72"] > dataframe["ema216"])
        ).astype(int)
        dataframe["enter_tag"] = "vol_expansion"
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = (
            (dataframe["volume"] > 0) & (dataframe["close"] < dataframe["bbmid"])
        ).astype(int)
        dataframe["exit_tag"] = "expansion_end"
        return dataframe

    def custom_stake_amount(
        self,
        pair,
        current_time,
        current_rate,
        proposed_stake,
        min_stake,
        max_stake,
        leverage,
        entry_tag,
        side,
        **kwargs,
    ) -> float:
        if side != "long" or leverage != 1.0:
            return 0.0
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe.empty:
            return 0.0
        volatility = float(dataframe.iloc[-1]["annvol"])
        if self.wallets is None:
            return 0.0
        capital = float(self.wallets.get_total_stake_amount())
        if not isfinite(volatility) or volatility <= 0 or not isfinite(capital) or capital <= 0:
            return 0.0
        fraction = min(0.495, 0.30 / volatility)
        size = min(proposed_stake, max_stake, capital * fraction)
        return float(size) if isfinite(size) and size >= (min_stake or 0.0) else 0.0

    def leverage(
        self,
        pair,
        current_time,
        current_rate,
        proposed_leverage,
        max_leverage,
        entry_tag,
        side,
        **kwargs,
    ) -> float:
        return 1.0
