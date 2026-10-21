"""Order execution, result interpretation and order modification, against a fake terminal."""

import pytest

from metatrader_client.order import (
    place_market_order,
    place_pending_order,
    modify_pending_order,
    modify_position,
    close_position,
    send_order,
)
from metatrader_client.types import TradeRequestActions, TradeReturnCodes
from .fakes import make_symbol, make_order, make_position


# ---------------------------------------------------------------------------
# Finding 1: retcode interpretation
# ---------------------------------------------------------------------------

def test_rejected_order_is_reported_as_error(fake_mt5, connection):
    fake_mt5.default_retcode = TradeReturnCodes.NO_MONEY.value

    result = place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1)

    assert result["error"] is True
    assert result["status"] == "rejected"
    assert result["retcode"] == 10019
    assert "NO_MONEY" in result["message"]


def test_missing_terminal_response_is_unknown_state(fake_mt5, connection):
    fake_mt5.results = [None]
    fake_mt5.error = (-10004, "No IPC connection")

    result = place_market_order(connection, type="SELL", symbol="EURUSD", volume=0.1)

    assert result["error"] is True
    assert result["status"] == "no_response"
    assert "-10004" in result["message"]


def test_done_market_order_is_filled_and_reconciled(fake_mt5, connection):
    result = place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1)

    assert result["error"] is False
    assert result["status"] == "done"
    assert result["retcode"] == 10009
    assert "position_id" in result


def test_partial_fill_is_distinguished(fake_mt5, connection):
    fake_mt5.results = [lambda request: fake_mt5.result_for(request, TradeReturnCodes.DONE_PARTIAL.value, volume=0.04)]

    result = place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1)

    assert result["error"] is False
    assert result["status"] == "partial"
    assert "Partially filled" in result["message"]


def test_done_with_smaller_volume_is_partial(fake_mt5, connection):
    fake_mt5.results = [lambda request: fake_mt5.result_for(request, TradeReturnCodes.DONE.value, volume=0.05)]

    result = place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1)

    assert result["status"] == "partial"


def test_pending_order_placed_status(fake_mt5, connection):
    fake_mt5.default_retcode = TradeReturnCodes.PLACED.value

    result = place_pending_order(connection, type="BUY", symbol="EURUSD", volume=0.1, price=1.09)

    assert result["error"] is False
    assert result["status"] == "placed"
    assert fake_mt5.sent[0]["type_filling"] == 2  # RETURN for pending orders


def test_return_codes_match_metatrader5_constants():
    import MetaTrader5 as mt5

    for name in dir(mt5):
        if name.startswith("TRADE_RETCODE_"):
            code = getattr(mt5, name)
            assert TradeReturnCodes.to_string(code) == name[len("TRADE_RETCODE_"):], name


# ---------------------------------------------------------------------------
# Finding 3: SL/TP on market orders, validated against the live quote
# ---------------------------------------------------------------------------

def test_market_buy_sends_valid_stop_loss_and_take_profit(fake_mt5, connection):
    fake_mt5.ticks["EURUSD"].ask = 1.1001

    result = place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1, stop_loss=1.09, take_profit=1.12)

    assert result["error"] is False
    request = fake_mt5.sent[0]
    assert request["price"] == 1.1001
    assert request["sl"] == 1.09
    assert request["tp"] == 1.12


def test_market_buy_rejects_stop_loss_above_price_without_sending(fake_mt5, connection):
    result = place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1, stop_loss=1.2)

    assert result["error"] is True
    assert "Stop loss" in result["message"]
    assert fake_mt5.sent == []


def test_market_sell_rejects_take_profit_above_price(fake_mt5, connection):
    result = place_market_order(connection, type="SELL", symbol="EURUSD", volume=0.1, take_profit=1.2)

    assert result["error"] is True
    assert fake_mt5.sent == []


def test_stop_distance_is_enforced(fake_mt5, connection):
    fake_mt5.symbols["EURUSD"] = make_symbol(trade_stops_level=50)  # 0.0005

    too_close = place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1, stop_loss=1.0998)
    far_enough = place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1, stop_loss=1.0990)

    assert too_close["error"] is True
    assert "minimum stop distance" in too_close["message"]
    assert far_enough["error"] is False


def test_mcp_and_client_expose_market_sl_tp():
    import inspect
    from metatrader_client.client_order import MT5Order
    from metatrader_mcp import server

    assert {"stop_loss", "take_profit"} <= set(inspect.signature(MT5Order.place_market_order).parameters)
    assert {"stop_loss", "take_profit"} <= set(inspect.signature(server.place_market_order).parameters)


# ---------------------------------------------------------------------------
# Finding 6: filling mode
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("exemode, mask, expected", [
    (0, 0, 0),   # request execution -> FOK
    (1, 0, 0),   # instant execution -> FOK
    (2, 1, 0),   # market, FOK allowed -> FOK
    (2, 2, 1),   # market, IOC allowed -> IOC
    (2, 6, 1),   # market, IOC + BOC -> IOC (BOC is never used for market orders)
    (3, 0, 2),   # exchange, nothing else -> RETURN
])
def test_market_filling_mode_selection(fake_mt5, connection, exemode, mask, expected):
    fake_mt5.symbols["EURUSD"] = make_symbol(trade_exemode=exemode, filling_mode=mask)

    result = place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1)

    assert result["error"] is False
    assert fake_mt5.sent[0]["type_filling"] == expected


@pytest.mark.parametrize("mask", [0, 4])
def test_market_execution_without_fok_or_ioc_is_rejected_cleanly(fake_mt5, connection, mask):
    fake_mt5.symbols["EURUSD"] = make_symbol(trade_exemode=2, filling_mode=mask)

    result = place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1)

    assert result["error"] is True
    assert "filling mode" in result["message"]
    assert fake_mt5.sent == []


# ---------------------------------------------------------------------------
# Finding 7 (symbol limits): volume, price precision
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("volume, fragment", [
    (0.001, "below the symbol minimum"),
    (51, "above the symbol maximum"),
    (0.015, "not a multiple"),
    (0, "greater than 0"),
])
def test_volume_is_validated_against_symbol(fake_mt5, connection, volume, fragment):
    result = place_market_order(connection, type="BUY", symbol="EURUSD", volume=volume)

    assert result["error"] is True
    assert fragment in result["message"]
    assert fake_mt5.sent == []


def test_price_precision_is_validated(fake_mt5, connection):
    result = place_pending_order(connection, type="BUY", symbol="EURUSD", volume=0.1, price=1.085347)

    assert result["error"] is True
    assert "nearest valid value is 1.08535" in result["message"]


def test_tick_size_is_validated(fake_mt5, connection):
    fake_mt5.symbols["EURUSD"] = make_symbol(digits=2, point=0.01, trade_tick_size=0.25)
    fake_mt5.ticks["EURUSD"].bid, fake_mt5.ticks["EURUSD"].ask = 100.0, 100.25

    result = place_pending_order(connection, type="BUY", symbol="EURUSD", volume=0.1, price=99.1)

    assert result["error"] is True
    assert "nearest valid value is 99.0" in result["message"]


@pytest.mark.parametrize("order_type, price, fragment", [
    ("BUY_LIMIT", 1.1005, "below the current ask"),
    ("SELL_LIMIT", 1.0995, "above the current bid"),
    ("BUY_STOP", 1.0995, "above the current ask"),
    ("SELL_STOP", 1.1005, "below the current bid"),
])
def test_pending_price_side_is_validated(fake_mt5, connection, order_type, price, fragment):
    result = send_order(connection, action=TradeRequestActions.PENDING, order_type=order_type, symbol="EURUSD", volume=0.1, price=price)

    assert result["success"] is False
    assert fragment in result["message"]


# ---------------------------------------------------------------------------
# Finding 2: modifying a pending order keeps its protections
# ---------------------------------------------------------------------------

def test_modify_pending_price_keeps_sl_tp_and_expiration(fake_mt5, connection):
    fake_mt5.orders = [make_order(55, type=2, price_open=1.09, sl=1.08, tp=1.10, type_time=2, time_expiration=1_800_000_000)]

    result = modify_pending_order(connection, id=55, price=1.0905)

    assert result["error"] is False
    request = fake_mt5.sent[0]
    assert request["price"] == 1.0905
    assert request["sl"] == 1.08
    assert request["tp"] == 1.10
    assert request["type_time"] == 2
    assert request["expiration"] == 1_800_000_000


def test_modify_pending_zero_removes_level_explicitly(fake_mt5, connection):
    fake_mt5.orders = [make_order(55, type=2, price_open=1.09, sl=1.08, tp=1.10)]

    modify_pending_order(connection, id=55, stop_loss=0)

    assert fake_mt5.sent[0]["sl"] == 0.0
    assert fake_mt5.sent[0]["tp"] == 1.10


def test_modify_pending_rejects_price_that_crosses_stop_loss(fake_mt5, connection):
    fake_mt5.orders = [make_order(55, type=2, price_open=1.09, sl=1.08)]

    result = modify_pending_order(connection, id=55, price=1.0795)

    assert result["error"] is True
    assert fake_mt5.sent == []


def test_modify_position_take_profit_only_keeps_stop_loss(fake_mt5, connection):
    fake_mt5.positions = [make_position(7, type=0, sl=1.095, tp=1.105)]

    result = modify_position(connection, 7, take_profit=1.11)

    assert result["error"] is False
    assert fake_mt5.sent[0]["sl"] == 1.095
    assert fake_mt5.sent[0]["tp"] == 1.11


def test_modify_position_no_changes_is_not_success(fake_mt5, connection):
    fake_mt5.positions = [make_position(7, type=0, sl=1.095, tp=1.105)]
    fake_mt5.default_retcode = TradeReturnCodes.NO_CHANGES.value

    result = modify_position(connection, 7, take_profit=1.105)

    assert result["error"] is True
    assert result["status"] == "no_changes"


# ---------------------------------------------------------------------------
# Closing a single position is reconciled with the terminal
# ---------------------------------------------------------------------------

def test_close_position_reports_remaining_volume(fake_mt5, connection):
    position = make_position(9, type=0, volume=0.1)
    fake_mt5.positions = [position]

    def partial_close(request, result):
        position.volume = 0.04

    fake_mt5.on_send = partial_close

    result = close_position(connection, 9)

    assert result["error"] is True
    assert "still open with 0.04" in result["message"]
    assert fake_mt5.sent[0]["position"] == 9
    assert fake_mt5.sent[0]["type"] == 1  # opposite side


def test_close_position_success(fake_mt5, connection):
    fake_mt5.positions = [make_position(9, type=1, volume=0.1)]
    fake_mt5.on_send = lambda request, result: fake_mt5.positions.clear()

    result = close_position(connection, 9)

    assert result["error"] is False
    assert fake_mt5.sent[0]["type"] == 0
