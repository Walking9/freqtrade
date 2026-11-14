"""Long-only BTC/ETH 20-day breakout with a 10-day channel exit.

4h CLOSED candles; signals execute on the following candle. No optimization,
DCA, shorting or leverage. Use the accompanying configuration and report.
Sizing budgets 1.5% of realized account capital per initial 10% stop, reduced
when four ATRs exceed that stop distance. Gaps can exceed this budget.
"""

from math import isfinite

import talib.abstract as ta
from pandas import DataFrame

from freqtrade.strategy import IStrategy


class RobustChannel(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "4h"
    startup_candle_count = 1000
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
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=20)
        # Exclude the signal candle from both channels.
        dataframe["upper"] = dataframe["high"].rolling(120, min_periods=120).max().shift(1)
        dataframe["lower"] = dataframe["low"].rolling(60, min_periods=60).min().shift(1)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = (
            (dataframe["volume"] > 0) & (dataframe["close"] > dataframe["upper"])
        ).astype(int)
        dataframe["enter_tag"] = "20d_breakout"
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = (
            (dataframe["volume"] > 0) & (dataframe["close"] < dataframe["lower"])
        ).astype(int)
        dataframe["exit_tag"] = "10d_channel"
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
        # Unknown inputs / unsupported deployment return zero instead of sizing up.
        if side != "long" or leverage != 1.0 or not isfinite(current_rate) or current_rate <= 0:
            return 0.0
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe.empty:
            return 0.0
        atr = float(dataframe.iloc[-1]["atr"])
        if not isfinite(atr) or atr <= 0:
            return 0.0
        if self.wallets is None:
            return 0.0
        capital = float(self.wallets.get_total_stake_amount())
        if not isfinite(capital) or capital <= 0:
            return 0.0
        fraction = min(0.40, 0.015 / max(abs(self.stoploss), 4 * atr / current_rate))
        stake = min(proposed_stake, max_stake, capital * fraction)
        return float(stake) if isfinite(stake) and stake >= (min_stake or 0.0) else 0.0

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
