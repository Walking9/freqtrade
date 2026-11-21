import json
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest
from pandas import DataFrame, Timestamp, date_range

from freqtrade.data.dataprovider import DataProvider
from freqtrade.enums import CandleType, ExitType, RunMode, SignalDirection, TradingMode
from freqtrade.persistence import LocalTrade
from freqtrade.util import dt_ts
from tests.conftest import get_patched_exchange
from user_data.strategies.rotation_four import RotationConsensus as rotation_module


RotationConsensus = rotation_module.RotationConsensus
BTC, ETH, SOL, BNB = RotationConsensus.UNIVERSE
DECISION = Timestamp("2026-11-20 00:00Z")
TIMEFRAME = RotationConsensus.timeframe
HOUR = timedelta(hours=1)


def candles(coin, direction, cutoff):
    hours = np.arange(801)
    trend = direction * (0.0013 - coin * 0.00022) * (hours - hours[-1])
    close = 100 * np.exp(trend + 0.003 * np.sin(hours / 3))
    return DataFrame(
        {
            "date": date_range(end=cutoff, periods=len(hours), freq="h"),
            "open": close,
            "high": close * 1.002,
            "low": close * 0.998,
            "close": close,
            "volume": 100.0,
        },
    )


def shifted_final_candle(frame, multiple):
    corrected = frame.copy()
    row = corrected.index[-1]
    for column in ("open", "high", "low", "close"):
        corrected.loc[row, column] *= multiple
    return corrected


class ReplayExchange:
    """Supply cached candles to a real Freqtrade DataProvider."""

    markets = {
        pair: {"base": pair.split("/")[0], "quote": "USDT"} for pair in RotationConsensus.UNIVERSE
    }

    def __init__(self, frames):
        self.frames = frames

    def klines(self, pair_key, copy=True):
        frame = self.frames[pair_key[0]]
        return frame.copy() if copy else frame


class RotationRuntime:
    def __init__(self, monkeypatch, frames, runmode=RunMode.DRY_RUN):
        self.monkeypatch = monkeypatch
        self.strategy = RotationConsensus(
            {
                "runmode": runmode,
                "dry_run": True,
                "timeframe": TIMEFRAME,
                "candle_type_def": CandleType.FUTURES,
                "trading_mode": TradingMode.FUTURES,
                "margin_mode": "isolated",
                "max_open_trades": 3,
                "stake_currency": "USDT",
                "exchange": {"pair_whitelist": RotationConsensus.UNIVERSE},
            }
        )
        self.exchange = ReplayExchange(frames)
        self.strategy.dp = DataProvider(self.strategy.config, self.exchange)
        self.strategy.bot_start()

    def run(self, seconds, *, decision=DECISION, pairs=RotationConsensus.UNIVERSE):
        now = (decision + timedelta(seconds=seconds)).to_pydatetime()
        self.monkeypatch.setattr("freqtrade.strategy.interface.dt_now", lambda: now)
        self.monkeypatch.setattr(rotation_module, "dt_now", lambda: now, raising=False)
        self.strategy.bot_loop_start(current_time=now)
        self.strategy.analyze(list(pairs))

    def entry(self, pair):
        result, _ = self.strategy.dp.get_analyzed_dataframe(pair, TIMEFRAME)
        return self.strategy.get_entry_signal(pair, TIMEFRAME, result)[0]

    def exits(self, pair, *, is_short=True):
        result, _ = self.strategy.dp.get_analyzed_dataframe(pair, TIMEFRAME)
        return bool(self.strategy.get_exit_signal(pair, TIMEFRAME, result, is_short=is_short)[1])


@pytest.fixture
def rotation_frames():
    return {
        pair: candles(index, -1, DECISION - HOUR)
        for index, pair in enumerate(RotationConsensus.UNIVERSE)
    }


@pytest.mark.parametrize("direction", [-1, 1])
def test_complete_snapshot_preserves_long_and_short_rank_signals(monkeypatch, direction):
    frames = {
        pair: candles(index, direction, DECISION - HOUR)
        for index, pair in enumerate(RotationConsensus.UNIVERSE)
    }
    runtime = RotationRuntime(monkeypatch, frames)
    runtime.run(5, pairs=(SOL, BTC, BNB, ETH))

    expected_entry = SignalDirection.SHORT if direction < 0 else SignalDirection.LONG
    for pair in (BTC, ETH, SOL):
        assert runtime.entry(pair) == expected_entry
        assert not runtime.exits(pair, is_short=direction < 0)
    assert runtime.entry(BNB) is None
    assert runtime.exits(BNB, is_short=direction < 0)


@pytest.mark.parametrize("late_pair", [BTC, ETH, SOL, BNB])
def test_late_peer_defers_cross_asset_decision_and_reanalyzes_same_candle(
    monkeypatch, rotation_frames, late_pair
):
    frames = dict(rotation_frames)
    frames[late_pair] = frames[late_pair].iloc[:-1]
    runtime = RotationRuntime(monkeypatch, frames)
    runtime.run(5)
    for pair in RotationConsensus.UNIVERSE:
        assert runtime.entry(pair) is None
        assert not runtime.exits(pair)

    frames[late_pair] = rotation_frames[late_pair]
    runtime.run(60)
    for pair in (BTC, ETH, SOL):
        assert runtime.entry(pair) == SignalDirection.SHORT
        assert not runtime.exits(pair)
    assert runtime.entry(BNB) is None
    assert runtime.exits(BNB)


def test_frozen_rank_ignores_later_revision_of_completed_snapshot(monkeypatch, rotation_frames):
    frames = dict(rotation_frames)
    runtime = RotationRuntime(monkeypatch, frames)
    runtime.run(5)
    assert runtime.exits(BNB)

    frames[BNB] = shifted_final_candle(frames[BNB], 0.50)
    revised = RotationRuntime(monkeypatch, frames)
    revised.run(20)
    assert revised.entry(BNB) == SignalDirection.SHORT
    assert not revised.exits(BNB)

    runtime.run(25, pairs=(BNB, SOL, ETH, BTC))
    assert runtime.entry(BNB) is None
    assert runtime.exits(BNB)


@pytest.mark.parametrize("seconds, allowed", [(300, True), (301, False)])
def test_snapshot_completion_deadline(monkeypatch, rotation_frames, seconds, allowed):
    frames = dict(rotation_frames)
    frames[BNB] = frames[BNB].iloc[:-1]
    runtime = RotationRuntime(monkeypatch, frames)
    runtime.run(5)
    frames[BNB] = rotation_frames[BNB]
    runtime.run(seconds)

    assert (runtime.entry(BTC) == SignalDirection.SHORT) is allowed
    assert runtime.exits(BNB) is allowed


def test_expired_cached_rank_exit_and_restarted_bot(monkeypatch, rotation_frames):
    runtime = RotationRuntime(monkeypatch, rotation_frames)
    runtime.run(5)
    assert runtime.exits(BNB)
    runtime.run(301)
    assert not runtime.exits(BNB)
    assert runtime.entry(BTC) is None

    restarted = RotationRuntime(monkeypatch, rotation_frames)
    restarted.run(301)
    assert not restarted.exits(BNB)
    assert restarted.entry(BTC) is None


def test_btc_reversal_recovers_without_full_rank_snapshot(monkeypatch, rotation_frames):
    frames = dict(rotation_frames)
    frames[BTC] = frames[BTC].iloc[:0]
    frames[BNB] = frames[BNB].iloc[:-1]
    runtime = RotationRuntime(monkeypatch, frames)
    runtime.run(5)
    assert not runtime.exits(SOL)

    frames[BTC] = shifted_final_candle(rotation_frames[BTC], 2)
    runtime.run(60)
    assert runtime.exits(SOL)
    assert runtime.entry(SOL) is None


def test_zero_volume_own_candle_correction_reenables_own_exit(monkeypatch, rotation_frames):
    frames = dict(rotation_frames)
    frames[BNB] = frames[BNB].iloc[:-1]
    frames[SOL] = rotation_frames[SOL].copy()
    frames[SOL].loc[frames[SOL].index[-1], "volume"] = 0.0
    runtime = RotationRuntime(monkeypatch, frames)
    runtime.run(5)
    assert not runtime.exits(SOL)
    assert runtime.entry(BTC) is None

    frames[SOL] = shifted_final_candle(rotation_frames[SOL], 2)
    runtime.run(60)
    assert runtime.exits(SOL)
    assert runtime.entry(BTC) is None


def test_zero_volume_peer_cannot_complete_snapshot(monkeypatch, rotation_frames):
    frames = dict(rotation_frames)
    frames[BNB] = shifted_final_candle(frames[BNB], 0.50)
    frames[BNB].loc[frames[BNB].index[-1], "volume"] = 0.0
    runtime = RotationRuntime(monkeypatch, frames)
    runtime.run(5)

    assert runtime.entry(BTC) is None
    assert not runtime.exits(SOL)
    assert not runtime.exits(BNB)


def test_own_reversal_requires_current_closed_candle(monkeypatch, rotation_frames):
    frames = dict(rotation_frames)
    frames[BTC] = frames[BTC].iloc[:-1]
    frames[SOL] = shifted_final_candle(frames[SOL], 2)
    runtime = RotationRuntime(monkeypatch, frames)
    runtime.run(5)
    assert runtime.exits(SOL)

    runtime.run(5, decision=DECISION + HOUR)
    assert not runtime.exits(SOL)


def test_forming_candle_does_not_complete_snapshot(monkeypatch, rotation_frames):
    frames = dict(rotation_frames)
    frame = frames[BNB].copy()
    frame.loc[frame.index[-1], "date"] += HOUR
    frames[BNB] = frame
    runtime = RotationRuntime(monkeypatch, frames)
    runtime.run(5)

    assert runtime.entry(BTC) is None
    assert not runtime.exits(SOL)
    assert not runtime.exits(BNB)


def test_stoploss_stays_active_when_rank_snapshot_is_pending(monkeypatch, rotation_frames):
    frames = dict(rotation_frames)
    frames[BNB] = frames[BNB].iloc[:-1]
    runtime = RotationRuntime(monkeypatch, frames)
    runtime.run(5)
    assert not runtime.exits(SOL)
    assert runtime.strategy.order_types["stoploss_on_exchange"]
    trade = LocalTrade(
        pair=SOL,
        is_short=True,
        leverage=1.0,
        open_rate=100.0,
        stake_amount=100.0,
        amount=1.0,
        fee_open=0.0005,
        fee_close=0.0005,
        open_date=(DECISION - HOUR).to_pydatetime(),
        enter_tag="vc_d=0.05000000",
    )
    trade.adjust_stop_loss(100.0, -0.05, initial=True)

    exits = runtime.strategy.should_exit(
        trade,
        106.0,
        (DECISION + timedelta(seconds=5)).to_pydatetime(),
        enter=False,
        exit_=False,
    )
    assert any(result.exit_type == ExitType.STOP_LOSS for result in exits)


def test_rotation_service_repolls_only_stale_candles(mocker, time_machine):
    example = (
        Path(__file__).resolve().parents[2] / "deploy/rotation-four/config.dryrun.example.json"
    )
    config = json.loads(example.read_text())
    config["runmode"] = RunMode.DRY_RUN
    exchange = get_patched_exchange(mocker, config, exchange="binance")

    decision = DECISION.to_pydatetime()
    late_key = (BNB, TIMEFRAME, CandleType.FUTURES)
    current_key = (BTC, TIMEFRAME, CandleType.FUTURES)
    exchange._pairs_last_refresh_time[late_key] = dt_ts(decision - 2 * HOUR)
    exchange._pairs_last_refresh_time[current_key] = dt_ts(decision - HOUR)
    exchange._pairs_last_poll_time[late_key] = dt_ts(decision + timedelta(seconds=16))

    time_machine.move_to(decision + timedelta(seconds=30), tick=False)
    assert not exchange._now_is_time_to_refresh(*late_key)
    for seconds in (31, 60):
        time_machine.move_to(decision + timedelta(seconds=seconds), tick=False)
        assert exchange._now_is_time_to_refresh(*late_key)
        assert not exchange._now_is_time_to_refresh(*current_key)
