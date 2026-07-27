# --- Do not remove these libs ---
from datetime import datetime

import talib.abstract as ta
from pandas import DataFrame

from freqtrade.strategy import DecimalParameter, IntParameter, IStrategy, Trade


class MeanReversionBB(IStrategy):
    """
    Mean reversion strategy (Bollinger Bands + RSI) for ranging / consolidating markets.

    Entry (long only):
      - Close touches or breaks below the lower Bollinger Band (oversold price)
      - RSI below oversold threshold
      - Bollinger band width below its recent average (volatility contraction,
        i.e. the market is in a box range, not a trending breakdown)

    Exit (scaled take profit):
      1. 50% partial exit when price reverts to the middle band (adjust_trade_position)
      2. Full exit of the remainder when price touches the upper band or RSI is overbought
      3. Trailing stop locks in profit once +3.5% is reached; hard stoploss caps risk at -5%

    Usage:
        freqtrade download-data --timeframe 15m --timerange 20250101- -p BTC/USDT ETH/USDT
        freqtrade backtesting --strategy MeanReversionBB --timeframe 15m
        freqtrade hyperopt --strategy MeanReversionBB --spaces buy sell \
            --hyperopt-loss SharpeHyperOptLoss -e 200
    """

    INTERFACE_VERSION = 3

    timeframe = "15m"
    can_short = False

    # Target roughly a lower-band -> upper-band traversal; shrink target over time
    minimal_roi = {
        "0": 0.06,
        "120": 0.04,
        "360": 0.02,
        "720": 0.01,
    }

    # Hard stoploss - a range bottom that keeps falling is a failed setup
    stoploss = -0.05

    # Trailing take profit: arms at +3.5%, then trails 1.5% behind the peak
    trailing_stop = True
    trailing_stop_positive = 0.015
    trailing_stop_positive_offset = 0.035
    trailing_only_offset_is_reached = True

    # Partial take profit is done in adjust_trade_position(); no DCA entries
    position_adjustment_enable = True
    max_entry_position_adjustment = 0

    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # BB(20) + 48-candle width average -> keep enough history
    startup_candle_count: int = 100

    # --- Hyperoptable parameters ---
    buy_rsi = IntParameter(15, 40, default=30, space="buy", optimize=True)
    # bb_width must be below (recent average width * factor) to qualify as "quiet range"
    buy_bb_width_factor = DecimalParameter(
        0.6, 1.2, decimals=2, default=1.0, space="buy", optimize=True
    )
    sell_rsi = IntParameter(60, 90, default=70, space="sell", optimize=True)

    # --- Fixed indicator settings ---
    bb_period = 20
    bb_std = 2.0
    bb_width_lookback = 48  # 12h on 15m candles

    order_types = {
        "entry": "limit",
        "exit": "limit",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }

    plot_config = {
        "main_plot": {
            "bb_upperband": {"color": "grey"},
            "bb_middleband": {"color": "orange"},
            "bb_lowerband": {"color": "grey"},
        },
        "subplots": {
            "RSI": {"rsi": {"color": "purple"}},
            "BB width": {
                "bb_width": {"color": "blue"},
                "bb_width_ma": {"color": "red"},
            },
        },
    }

    @property
    def protections(self):
        # Ranging strategies get chopped up when the range breaks - cool off after stops
        return [
            {"method": "CooldownPeriod", "stop_duration_candles": 4},
            {
                "method": "StoplossGuard",
                "lookback_period_candles": 96,
                "trade_limit": 3,
                "stop_duration_candles": 24,
                "only_per_pair": False,
            },
        ]

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)

        bollinger = ta.BBANDS(
            dataframe,
            timeperiod=self.bb_period,
            nbdevup=self.bb_std,
            nbdevdn=self.bb_std,
        )
        dataframe["bb_lowerband"] = bollinger["lowerband"]
        dataframe["bb_middleband"] = bollinger["middleband"]
        dataframe["bb_upperband"] = bollinger["upperband"]

        # Relative band width as volatility proxy; compare against its own recent average
        dataframe["bb_width"] = (dataframe["bb_upperband"] - dataframe["bb_lowerband"]) / dataframe[
            "bb_middleband"
        ]
        dataframe["bb_width_ma"] = dataframe["bb_width"].rolling(self.bb_width_lookback).mean()

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                (dataframe["close"] <= dataframe["bb_lowerband"])
                & (dataframe["rsi"] < self.buy_rsi.value)
                & (
                    dataframe["bb_width"]
                    < dataframe["bb_width_ma"] * self.buy_bb_width_factor.value
                )
                & (dataframe["volume"] > 0)
            ),
            ["enter_long", "enter_tag"],
        ] = (1, "bb_lower_oversold")

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            ((dataframe["close"] >= dataframe["bb_upperband"]) & (dataframe["volume"] > 0)),
            ["exit_long", "exit_tag"],
        ] = (1, "bb_upper")

        dataframe.loc[
            ((dataframe["rsi"] > self.sell_rsi.value) & (dataframe["volume"] > 0)),
            ["exit_long", "exit_tag"],
        ] = (1, "rsi_overbought")

        return dataframe

    def adjust_trade_position(
        self,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        min_stake: float | None,
        max_stake: float,
        current_entry_rate: float,
        current_exit_rate: float,
        current_entry_profit: float,
        current_exit_profit: float,
        **kwargs,
    ) -> float | None | tuple[float | None, str | None]:
        """
        First take-profit batch: sell 50% once price reverts to the middle band
        with at least +1% profit. The remainder rides to the upper band / RSI
        overbought / trailing stop.
        """
        if trade.nr_of_successful_exits > 0:
            # Partial exit already taken
            return None
        if current_profit < 0.01:
            return None

        dataframe, _ = self.dp.get_analyzed_dataframe(trade.pair, self.timeframe)
        if dataframe.empty:
            return None
        last_candle = dataframe.iloc[-1].squeeze()

        if current_rate < last_candle["bb_middleband"]:
            return None

        half_stake = trade.stake_amount * 0.5
        # Both the exit chunk and the remaining position must stay tradeable
        if min_stake and half_stake < min_stake:
            return None

        return -half_stake, "partial_tp_middle_band"
