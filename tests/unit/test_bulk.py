"""Finding 5: bulk operations report every item and verify what remains."""

from metatrader_client.order import (
    close_all_positions,
    close_all_profitable_positions,
    close_all_losing_positions,
    close_all_positions_by_symbol,
    cancel_all_pending_orders,
)
from metatrader_client.types import TradeReturnCodes
from .fakes import make_position, make_order, make_symbol, make_tick


def remove_closed(fake_mt5):
    def on_send(request, result):
        if result is not None and result.retcode == TradeReturnCodes.DONE.value:
            fake_mt5.positions = [p for p in fake_mt5.positions if p.ticket != request.get("position")]
            fake_mt5.orders = [o for o in fake_mt5.orders if o.ticket != request.get("order")]
    return on_send


def test_failed_closes_are_not_reported_as_success(fake_mt5, connection):
    fake_mt5.positions = [make_position(1), make_position(2)]
    fake_mt5.default_retcode = TradeReturnCodes.MARKET_CLOSED.value

    result = close_all_positions(connection)

    assert result["error"] is True
    assert result["data"]["failed"] == [1, 2]
    assert result["data"]["remaining"] == [1, 2]
    assert "Closed 0 of 2" in result["message"]
    assert all(r["retcode"] == 10018 for r in result["data"]["results"])


def test_mixed_results_are_reported_per_position(fake_mt5, connection):
    fake_mt5.positions = [make_position(1), make_position(2)]
    fake_mt5.on_send = remove_closed(fake_mt5)
    fake_mt5.results = [
        lambda request: fake_mt5.result_for(request, TradeReturnCodes.DONE.value),
        lambda request: fake_mt5.result_for(request, TradeReturnCodes.REQUOTE.value),
    ]

    result = close_all_positions(connection)

    assert result["error"] is True
    assert result["data"]["succeeded"] == [1]
    assert result["data"]["failed"] == [2]
    assert result["data"]["remaining"] == [2]


def test_all_closed(fake_mt5, connection):
    fake_mt5.positions = [make_position(1), make_position(2)]
    fake_mt5.on_send = remove_closed(fake_mt5)

    result = close_all_positions(connection)

    assert result["error"] is False
    assert result["message"] == "Closed 2 of 2 positions"


def test_success_but_still_present_is_an_error(fake_mt5, connection):
    fake_mt5.positions = [make_position(1)]

    result = close_all_positions(connection)

    assert result["error"] is True
    assert result["data"]["remaining"] == [1]


def test_no_positions(fake_mt5, connection):
    result = close_all_positions(connection)

    assert result["error"] is False
    assert result["message"] == "No positions to process"


def test_filters_only_target_matching_positions(fake_mt5, connection):
    fake_mt5.positions = [
        make_position(1, profit=5.0),
        make_position(2, profit=-3.0),
        make_position(3, profit=1.0, symbol="GBPUSD"),
    ]
    fake_mt5.symbols["GBPUSD"] = make_symbol(name="GBPUSD")
    fake_mt5.ticks["GBPUSD"] = make_tick(1.3000, 1.3002)
    fake_mt5.on_send = remove_closed(fake_mt5)

    assert close_all_losing_positions(connection)["data"]["succeeded"] == [2]
    assert close_all_positions_by_symbol(connection, "GBPUSD")["data"]["succeeded"] == [3]
    assert close_all_profitable_positions(connection)["data"]["succeeded"] == [1]


def test_cancel_all_pending_orders_reports_failures(fake_mt5, connection):
    fake_mt5.orders = [make_order(10), make_order(11)]
    fake_mt5.on_send = remove_closed(fake_mt5)
    fake_mt5.results = [
        lambda request: fake_mt5.result_for(request, TradeReturnCodes.DONE.value),
        None,
    ]

    result = cancel_all_pending_orders(connection)

    assert result["error"] is True
    assert result["data"]["succeeded"] == [10]
    assert result["data"]["failed"] == [11]
    statuses = {r["id"]: r["status"] for r in result["data"]["results"]}
    assert statuses[11] == "no_response"


def test_unreadable_positions_are_an_error(fake_mt5, connection, monkeypatch):
    monkeypatch.setattr(fake_mt5, "positions_get", lambda **kwargs: None)
    fake_mt5.error = (-10004, "No IPC connection")

    result = close_all_positions(connection)

    assert result["error"] is True
    assert "Cannot read positions" in result["message"]


def test_unverifiable_result_is_an_error(fake_mt5, connection, monkeypatch):
    fake_mt5.positions = [make_position(1)]
    # bulk listing, close_position lookup, close_position check (closed), bulk verification (fails)
    listings = iter([fake_mt5.positions_get(), fake_mt5.positions_get(ticket=1), (), None])
    monkeypatch.setattr(fake_mt5, "positions_get", lambda **kwargs: next(listings))
    fake_mt5.error = (-10004, "No IPC connection")

    result = close_all_positions(connection)

    assert result["error"] is True
    assert result["data"]["remaining"] is None
    assert "could not verify" in result["message"]
