"""Risk limits, live account protection and duplicate prevention."""

from types import SimpleNamespace

import pytest

from metatrader_client.account.get_account_type import get_account_type
from metatrader_client.order import place_market_order, place_pending_order, modify_position, close_position
from metatrader_client.risk import RiskConfig
from metatrader_client.types import TradeReturnCodes
from .fakes import make_position, make_order, with_risk


# ---------------------------------------------------------------------------
# Account type
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("trade_mode, expected", [(0, "demo"), (1, "contest"), (2, "real")])
def test_account_type_matches_metatrader5_constants(monkeypatch, trade_mode, expected):
    import importlib

    module = importlib.import_module("metatrader_client.account.get_account_type")
    monkeypatch.setattr(module, "get_account_info", lambda connection: {"trade_mode": trade_mode})
    assert get_account_type(None) == expected


def test_account_constants_from_package():
    import MetaTrader5 as mt5

    assert (mt5.ACCOUNT_TRADE_MODE_DEMO, mt5.ACCOUNT_TRADE_MODE_CONTEST, mt5.ACCOUNT_TRADE_MODE_REAL) == (0, 1, 2)


# ---------------------------------------------------------------------------
# Live accounts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("trade_mode", [1, 2])
def test_non_demo_accounts_are_blocked_by_default(fake_mt5, connection, trade_mode):
    fake_mt5.account.trade_mode = trade_mode

    result = place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1)

    assert result["error"] is True
    assert "not a demo account" in result["message"]
    assert fake_mt5.sent == []


def test_live_trading_requires_explicit_opt_in(fake_mt5, connection):
    fake_mt5.account.trade_mode = 2
    with_risk(connection, allow_live_trading=True, duplicate_window_seconds=0)

    assert place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1)["error"] is False


# ---------------------------------------------------------------------------
# Exposure limits
# ---------------------------------------------------------------------------

def test_max_volume_per_order(fake_mt5, connection):
    with_risk(connection, max_volume_per_order=0.5)

    result = place_market_order(connection, type="BUY", symbol="EURUSD", volume=1)

    assert result["error"] is True
    assert "per-order limit" in result["message"]


def test_max_open_positions_counts_pending_orders(fake_mt5, connection):
    fake_mt5.positions = [make_position(1)]
    fake_mt5.orders = [make_order(2)]
    with_risk(connection, max_open_positions=2)

    result = place_pending_order(connection, type="BUY", symbol="EURUSD", volume=0.1, price=1.09)

    assert result["error"] is True
    assert "limit of 2" in result["message"]


def test_max_total_volume(fake_mt5, connection):
    fake_mt5.positions = [make_position(1, volume=0.8)]
    with_risk(connection, max_total_volume=1.0)

    assert place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.3)["error"] is True
    assert place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.2)["error"] is False


def test_closing_is_not_blocked_by_exposure_limits(fake_mt5, connection):
    fake_mt5.positions = [make_position(1, volume=5)]
    fake_mt5.on_send = lambda request, result: fake_mt5.positions.clear()
    with_risk(connection, max_volume_per_order=0.1, max_total_volume=0.1, max_open_positions=0, max_daily_loss=1)

    assert close_position(connection, 1)["error"] is False


# ---------------------------------------------------------------------------
# Risk per trade
# ---------------------------------------------------------------------------

def test_risk_per_trade_requires_stop_loss(fake_mt5, connection):
    with_risk(connection, max_risk_per_trade_pct=1)

    result = place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1)

    assert result["error"] is True
    assert "stop loss is required" in result["message"]


def test_risk_per_trade_limits_loss_at_stop(fake_mt5, connection):
    # Entry at ask 1.1001, 0.1 lot, 100 pips (0.0100) -> 100 USD = 1% of 10 000
    with_risk(connection, max_risk_per_trade_pct=0.5)
    too_risky = place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1, stop_loss=1.0901)

    with_risk(connection, max_risk_per_trade_pct=2)
    allowed = place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1, stop_loss=1.0901)

    assert too_risky["error"] is True
    assert "of equity" in too_risky["message"]
    assert allowed["error"] is False


def test_removing_stop_loss_is_blocked_when_required(fake_mt5, connection):
    fake_mt5.positions = [make_position(7, sl=1.095)]
    with_risk(connection, require_stop_loss=True)

    result = modify_position(connection, 7, stop_loss=0)

    assert result["error"] is True
    assert fake_mt5.sent == []


# ---------------------------------------------------------------------------
# Daily loss
# ---------------------------------------------------------------------------

def test_daily_loss_limit_blocks_new_orders(fake_mt5, connection):
    day_start = fake_mt5.ticks["EURUSD"].time - fake_mt5.ticks["EURUSD"].time % 86400
    fake_mt5.deals = [
        SimpleNamespace(ticket=1, position_id=1, time=day_start + 60, type=1, profit=-150.0, commission=-2.0, swap=0.0, fee=0.0),
        SimpleNamespace(ticket=2, position_id=2, time=day_start - 60, type=1, profit=-900.0, commission=0.0, swap=0.0, fee=0.0),
    ]
    fake_mt5.positions = [make_position(3, profit=-50.0)]
    with_risk(connection, max_daily_loss=200)

    result = place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1)

    assert result["error"] is True
    assert "Daily loss limit" in result["message"]

    with_risk(connection, max_daily_loss=300)
    assert place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1)["error"] is False


# ---------------------------------------------------------------------------
# Duplicate requests
# ---------------------------------------------------------------------------

def test_identical_request_within_window_is_rejected(fake_mt5, connection):
    with_risk(connection, duplicate_window_seconds=60)

    first = place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1)
    second = place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1)
    different = place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.2)

    assert first["error"] is False
    assert second["error"] is True
    assert "Identical request" in second["message"]
    assert different["error"] is False
    assert len(fake_mt5.sent) == 2


def test_unknown_outcome_blocks_blind_retry(fake_mt5, connection):
    with_risk(connection, duplicate_window_seconds=60)
    fake_mt5.results = [None]

    place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1)
    retry = place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1)

    assert retry["error"] is True
    assert len(fake_mt5.sent) == 1


def test_rejected_request_can_be_retried(fake_mt5, connection):
    with_risk(connection, duplicate_window_seconds=60)
    fake_mt5.results = [lambda request: fake_mt5.result_for(request, TradeReturnCodes.REQUOTE.value)]

    place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1)
    retry = place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1)

    assert retry["error"] is False
    assert len(fake_mt5.sent) == 2


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def test_config_from_environment():
    config = RiskConfig.from_sources(env={
        "MT5_ALLOW_LIVE_TRADING": "false",
        "MT5_MAX_VOLUME_PER_ORDER": "0.5",
        "MT5_MAX_OPEN_POSITIONS": "3",
        "MT5_MAX_DAILY_LOSS": "250",
        "MT5_REQUIRE_STOP_LOSS": "yes",
    })

    assert config.allow_live_trading is False
    assert config.max_volume_per_order == 0.5
    assert config.max_open_positions == 3
    assert config.max_daily_loss == 250
    assert config.require_stop_loss is True
    assert config.duplicate_window_seconds == 10


def test_config_mapping_overrides_environment():
    config = RiskConfig.from_sources({"max_volume_per_order": 1}, env={"MT5_MAX_VOLUME_PER_ORDER": "0.5"})
    assert config.max_volume_per_order == 1


@pytest.mark.parametrize("settings", [{"unknown": 1}, {"max_daily_loss": -1}, {"allow_live_trading": "maybe"}])
def test_invalid_config_is_rejected(settings):
    with pytest.raises(ValueError):
        RiskConfig.from_sources(settings, env={})


def test_connection_carries_risk_guard(monkeypatch):
    from metatrader_client.client_connection import MT5Connection

    monkeypatch.setenv("MT5_MAX_VOLUME_PER_ORDER", "0.3")
    connection = MT5Connection({"risk": {"max_daily_loss": 100}})

    assert connection.risk_guard.config.max_volume_per_order == 0.3
    assert connection.risk_guard.config.max_daily_loss == 100
