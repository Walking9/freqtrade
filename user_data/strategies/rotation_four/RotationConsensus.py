"""Four-asset risk-state-gated relative-momentum rotation, symmetric long/short.

Risk profile: RotationConsensus = 1x, 1.5% planned stop risk per trade;
RotationConsensus2x = 2x, 3%. Both cap margin at 33%, max three positions.
Rank by trailing 28d return / prior 7d hourly volatility; rotate every 8h.
Entry also requires BTC market consensus in the same direction.
The signal and closed-candle-only trailing logic are identical across profiles.
"""

import logging
import math
from datetime import timedelta

import numpy as np
from pandas import DataFrame, Timedelta, concat

from freqtrade.enums import RunMode
from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy, informative, stoploss_from_absolute


logger = logging.getLogger(__name__)


class RotationConsensus(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "1h"
    can_short = True
    startup_candle_count = 800
    process_only_new_candles = True
    stoploss = -0.12
    minimal_roi = {}
    trailing_stop = False
    use_custom_stoploss = True
    use_exit_signal = True
    exit_profit_only = False
    position_adjustment_enable = False
    ignore_buying_expired_candle_after = 300
    LEVERAGE = 1.0
    RISK_FRACTION = 0.015
    MARGIN_CAP = 0.33
    HORIZONS = (168, 336, 672)
    ENTRY_THRESHOLD = 0.25
    ATR_MULTIPLE = 6.0
    UNIVERSE = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "BNB/USDT:USDT")
    order_types = {
        "entry": "market",
        "exit": "market",
        "stoploss": "market",
        "stoploss_on_exchange": True,
        "stoploss_on_exchange_interval": 60,
        "stoploss_price_type": "last",
    }
    order_time_in_force = {"entry": "GTC", "exit": "GTC"}

    def version(self):
        return "1.1-four-assets-20261114"

    def bot_start(self, **kwargs):
        if (
            self.config.get("trading_mode") != "futures"
            or self.config.get("margin_mode") != "isolated"
        ):
            raise ValueError("RotationConsensus requires isolated futures.")
        if self.timeframe != "1h":
            raise ValueError("RotationConsensus requires 1h candles.")
        if self.config.get("runmode") in (RunMode.DRY_RUN, RunMode.LIVE):
            if self.config.get("max_open_trades") != 3:
                raise ValueError("Expected max_open_trades=3.")
            if set(self.config["exchange"]["pair_whitelist"]) != set(self.UNIVERSE):
                raise ValueError("Expected frozen four-asset universe.")

    @informative("1h", "BTC/USDT:USDT", fmt="market_{column}", ffill=False)
    def market_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        f = dataframe.copy()
        log = np.log(f.close.where(f.close > 0))
        sigma = log.diff().rolling(168, min_periods=168).std().shift(1)
        f["consensus"] = sum(
            np.tanh((log - log.shift(h)) / (sigma * np.sqrt(h)).replace(0, np.nan))
            for h in self.HORIZONS
        ) / len(self.HORIZONS)
        return f[["date", "consensus"]]

    def informative_pairs(self):
        return [(pair, "1h") for pair in self.UNIVERSE]

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        f = dataframe.copy()
        log = np.log(f.close.where(f.close > 0))
        sigma = log.diff().rolling(168, min_periods=168).std().shift(1)
        f["consensus"] = sum(
            np.tanh((log - log.shift(h)) / (sigma * np.sqrt(h)).replace(0, np.nan))
            for h in self.HORIZONS
        ) / len(self.HORIZONS)
        true_range = concat(
            [f.high - f.low, (f.high - f.close.shift()).abs(), (f.low - f.close.shift()).abs()],
            axis=1,
        ).max(axis=1)
        f["distance"] = (
            self.ATR_MULTIPLE * true_range.rolling(24, min_periods=24).mean() / f.close
        ).clip(0.03, 0.12)
        f["history_ok"] = (
            f.date.diff().eq(Timedelta(hours=1)).rolling(672, min_periods=672).sum().eq(672)
        )
        pieces = []
        for pair in self.UNIVERSE:
            reference = self.dp.get_pair_dataframe(pair, "1h")
            if reference.empty:
                f["relative_rank"] = np.nan
                f["universe_valid"] = False
                break
            prices = np.log(reference.close.where(reference.close > 0))
            vol = prices.diff().rolling(168, min_periods=168).std().shift(1)
            strength = (prices - prices.shift(672)) / vol.replace(0, np.nan)
            pieces.append(strength.set_axis(reference.date).rename(pair))
        else:
            panel = concat(pieces, axis=1, sort=True)
            valid = panel.notna().all(axis=1)
            ranks = panel.rank(axis=1, method="first")
            lookup = DataFrame(
                {
                    "date": ranks.index,
                    "relative_rank": ranks[metadata["pair"]].to_numpy(),
                    "universe_valid": valid.to_numpy(),
                }
            )
            f = f.merge(lookup, on="date", how="left", validate="one_to_one")
            f["universe_valid"] = f.universe_valid.eq(True)
        decision = f.date + Timedelta(hours=1)
        f["rebalance"] = decision.dt.hour.mod(8).eq(0)
        return f

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        f = dataframe.copy()
        ready = (
            f.history_ok
            & f.volume.gt(0)
            & f.distance.notna()
            & f.market_date.eq(f.date)
            & f.market_consensus.notna()
            & f.universe_valid
            & f.rebalance
        )
        f["enter_long"] = (
            ready
            & f.consensus.ge(self.ENTRY_THRESHOLD)
            & f.market_consensus.gt(0)
            & f.relative_rank.ge(len(self.UNIVERSE) - 2)
        ).astype(int)
        f["enter_short"] = (
            ready
            & f.consensus.le(-self.ENTRY_THRESHOLD)
            & f.market_consensus.lt(0)
            & f.relative_rank.le(3)
        ).astype(int)
        f["enter_tag"] = ""
        active = f.enter_long.eq(1) | f.enter_short.eq(1)
        f.loc[active, "enter_tag"] = f.loc[active, "distance"].map(lambda x: f"vc_d={x:.8f}")
        if self.config.get("runmode") in (RunMode.DRY_RUN, RunMode.LIVE) and not f.empty:
            row = f.iloc[-1]
            logger.info(
                "RotationConsensus %s candle=%s score=%.3f market=%.3f rank=%s "
                "rebalance=%s ready=%s long=%s short=%s distance=%.4f",
                metadata["pair"],
                row.date,
                row.consensus,
                row.market_consensus,
                row.relative_rank,
                bool(row.rebalance),
                bool(ready.iloc[-1]),
                int(row.enter_long),
                int(row.enter_short),
                row.distance,
            )
        return f

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        f = dataframe.copy()
        f["exit_long"] = (
            (
                f.consensus.le(0)
                | (
                    f.rebalance
                    & (
                        ~f.universe_valid
                        | f.relative_rank.lt(len(self.UNIVERSE) - 2)
                        | f.market_consensus.le(0)
                    )
                )
            )
            & f.volume.gt(0)
        ).astype(int)
        f["exit_short"] = (
            (
                f.consensus.ge(0)
                | (
                    f.rebalance
                    & (~f.universe_valid | f.relative_rank.gt(3) | f.market_consensus.ge(0))
                )
            )
            & f.volume.gt(0)
        ).astype(int)
        f["exit_tag"] = "consensus_reversed"
        return f

    @staticmethod
    def _distance(tag):
        try:
            if not isinstance(tag, str) or not tag.startswith("vc_d="):
                return None
            distance = float(tag[5:])
            return (
                distance
                if math.isfinite(distance) and 0.03 - 1e-8 <= distance <= 0.12 + 1e-8
                else None
            )
        except (TypeError, ValueError):
            return None

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
    ):
        return min(self.LEVERAGE, max_leverage)

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
    ):
        try:
            d = self._distance(entry_tag)
            equity = float(self.wallets.get_total_stake_amount())
            available = float(self.wallets.get_available_stake_amount())
            if d is None or leverage != self.LEVERAGE:
                return 0.0
            if not all(
                math.isfinite(x) and x > 0 for x in [equity, available, current_rate, max_stake]
            ):
                return 0.0
            amount = min(
                equity * self.MARGIN_CAP,
                equity * self.RISK_FRACTION / (d * leverage),
                available,
                max_stake,
            )
            if min_stake is not None and (not math.isfinite(min_stake) or amount < min_stake):
                logger.debug(
                    "RotationConsensus %s refuses undersized entry: desired=%s minimum=%s",
                    pair,
                    amount,
                    min_stake,
                )
                return 0.0
            return amount if math.isfinite(amount) and amount > 0 else 0.0
        except Exception:
            logger.exception("RotationConsensus sizing failed; refusing entry")
            return 0.0

    def confirm_trade_entry(
        self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs
    ):
        try:
            if self._distance(entry_tag) is None:
                return False
            recent = Trade.get_trades_proxy(
                pair=pair, is_open=False, close_date=current_time - timedelta(hours=7)
            )
            for trade in recent:
                if trade.exit_reason in (
                    "stop_loss",
                    "trailing_stop_loss",
                    "stoploss_on_exchange",
                    "liquidation",
                ):
                    stopped = trade.close_date_utc.replace(minute=0, second=0, microsecond=0)
                    if current_time < stopped + timedelta(hours=6):
                        return False
            return True
        except Exception:
            logger.exception("RotationConsensus cooldown state unavailable; refusing entry")
            return False

    def custom_stoploss(
        self, pair, trade, current_time, current_rate, current_profit, after_fill, **kwargs
    ):
        distance = self._distance(trade.enter_tag)
        if distance is None:
            return None
        sign = -1 if trade.is_short else 1
        stop = trade.open_rate * (1 - sign * distance)
        try:
            f, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            entry_candle = trade.open_date_utc.replace(minute=0, second=0, microsecond=0)
            # Explicit timestamp cutoff: never use current unfinished candle or intrabar high.
            current_candle = current_time.replace(minute=0, second=0, microsecond=0)
            closed = f[(f.date >= entry_candle) & (f.date < current_candle)]
            if not closed.empty:
                peak = float(closed.close.min() if trade.is_short else closed.close.max())
                if sign * (peak / trade.open_rate - 1) >= distance:
                    candidate = peak * (1 - sign * distance)
                    stop = min(stop, candidate) if trade.is_short else max(stop, candidate)
            return stoploss_from_absolute(
                stop, current_rate=current_rate, is_short=trade.is_short, leverage=trade.leverage
            )
        except Exception:
            logger.exception("RotationConsensus trailing data unavailable; retaining existing stop")
            return None


class RotationConsensus2x(RotationConsensus):
    LEVERAGE = 2.0
    RISK_FRACTION = 0.03
    stoploss = -0.24
