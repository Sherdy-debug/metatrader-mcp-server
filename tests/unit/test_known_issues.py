"""Regression tests for docs/KNOWN_ISSUES.md (MT5-001, MT5-002, MT5-003)."""

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

import metatrader_client.risk as risk
from metatrader_client.order import close_position, close_all_positions, place_market_order, send_order
from metatrader_client.risk import RiskConfig, RiskGuard
from metatrader_client.types import TradeRequestActions, TradeReturnCodes
from .fakes import FakeMT5, make_position, with_risk

TIMEOUT = 5  # safety net only: the tests synchronize on events, never on sleeps


def unreadable(fake_mt5, monkeypatch, function):
    monkeypatch.setattr(fake_mt5, function, lambda **kwargs: None)
    fake_mt5.error = (-10004, "No IPC connection")


def add_position_on_send(fake_mt5):
    """Make the fake terminal list the position opened by each successful market order."""
    def on_send(request, result):
        if result is not None and result.retcode == TradeReturnCodes.DONE.value and not request.get("position"):
            fake_mt5.positions.append(make_position(result.order, volume=request["volume"]))
    return on_send


# ---------------------------------------------------------------------------
# MT5-001: unreadable state blocks new exposure
# ---------------------------------------------------------------------------

def test_mt5_001_reproduction_check_new_order_blocks_when_state_is_unreadable(monkeypatch):
    fake = FakeMT5()
    unreadable(fake, monkeypatch, "positions_get")
    unreadable(fake, monkeypatch, "orders_get")
    guard = RiskGuard(RiskConfig(max_total_volume=0.05, max_open_positions=1))

    error = guard.check_new_order(fake, symbol="EURUSD", side=0, volume=0.01, entry_price=1.1001, stop_loss=None)

    assert error is not None
    assert "positions_get failed (error -10004: No IPC connection)" in error


@pytest.mark.parametrize("function", ["positions_get", "orders_get"])
@pytest.mark.parametrize("limit", [{"max_open_positions": 5}, {"max_total_volume": 5.0}])
def test_mt5_001_unreadable_listing_blocks_opening_without_sending(fake_mt5, connection, monkeypatch, function, limit):
    unreadable(fake_mt5, monkeypatch, function)
    with_risk(connection, duplicate_window_seconds=0, **limit)

    result = place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1)

    assert result["error"] is True
    assert "Cannot verify exposure limits" in result["message"]
    assert f"{function} failed" in result["message"]
    assert fake_mt5.sent == []


def test_mt5_001_valid_empty_listings_allow_opening(fake_mt5, connection):
    with_risk(connection, max_open_positions=1, max_total_volume=1.0, duplicate_window_seconds=0)

    assert place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1)["error"] is False
    assert len(fake_mt5.sent) == 1


def test_mt5_001_unreadable_positions_block_daily_loss_check(fake_mt5, connection, monkeypatch):
    fake_mt5.deals = []  # history is readable and shows no loss
    unreadable(fake_mt5, monkeypatch, "positions_get")
    with_risk(connection, max_daily_loss=100, duplicate_window_seconds=0)

    result = place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1)

    assert result["error"] is True
    assert "Cannot verify the daily loss limit" in result["message"]
    assert fake_mt5.sent == []


def test_mt5_001_unreadable_history_blocks_daily_loss_check(fake_mt5, connection, monkeypatch):
    monkeypatch.setattr(fake_mt5, "history_deals_get", lambda *args, **kwargs: None)
    with_risk(connection, max_daily_loss=100, duplicate_window_seconds=0)

    result = place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1)

    assert result["error"] is True
    assert "history_deals_get failed" in result["message"]
    assert fake_mt5.sent == []


def test_mt5_001_limits_disabled_do_not_need_the_listings(fake_mt5, connection, monkeypatch):
    unreadable(fake_mt5, monkeypatch, "orders_get")

    assert place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1)["error"] is False


# ---------------------------------------------------------------------------
# MT5-002: a close is only confirmed by a valid listing without the position
# ---------------------------------------------------------------------------

def listing_sequence(fake_mt5, monkeypatch, *listings):
    sequence = iter(listings)
    monkeypatch.setattr(fake_mt5, "positions_get", lambda **kwargs: next(sequence))


def test_mt5_002_reproduction_failed_final_check_is_not_a_confirmed_close(fake_mt5, connection, monkeypatch):
    position = make_position(42)
    listing_sequence(fake_mt5, monkeypatch, (position,), None)
    fake_mt5.error = (-10004, "No IPC connection")

    result = close_position(connection, 42)

    assert result["error"] is True
    assert result["verification"] == "unknown"
    assert not result["message"].startswith("Closed position")
    assert "could not be verified" in result["message"]
    # The outcome of the request is kept for reconciliation, and nothing is retried.
    assert result["status"] == "done"
    assert result["retcode"] == TradeReturnCodes.DONE.value
    assert result["position_id"] == 42
    assert result["data"] is not None
    assert len(fake_mt5.sent) == 1


def test_mt5_002_empty_final_listing_confirms_close(fake_mt5, connection, monkeypatch):
    listing_sequence(fake_mt5, monkeypatch, (make_position(42),), ())

    result = close_position(connection, 42)

    assert result["error"] is False
    assert result["verification"] == "confirmed"


def test_mt5_002_remaining_volume_is_reported_with_partial_status(fake_mt5, connection, monkeypatch):
    listing_sequence(fake_mt5, monkeypatch, (make_position(42, volume=0.1),), (make_position(42, volume=0.06),))
    fake_mt5.results = [lambda request: fake_mt5.result_for(request, TradeReturnCodes.DONE_PARTIAL.value, volume=0.04)]

    result = close_position(connection, 42)

    assert result["error"] is True
    assert result["verification"] == "remaining"
    assert result["remaining_volume"] == 0.06
    assert result["status"] == "partial"


def test_mt5_002_no_response_is_unknown_and_not_retried(fake_mt5, connection):
    fake_mt5.positions = [make_position(42)]
    fake_mt5.results = [None]

    result = close_position(connection, 42)

    assert result["error"] is True
    assert result["verification"] == "unknown"
    assert result["status"] == "no_response"
    assert len(fake_mt5.sent) == 1


def test_mt5_002_bulk_keeps_unverified_closes_out_of_succeeded(fake_mt5, connection, monkeypatch):
    # bulk listing, close lookup, close check (fails), bulk final listing (fails)
    listing_sequence(fake_mt5, monkeypatch, (make_position(42),), (make_position(42),), None, None)

    result = close_all_positions(connection)

    assert result["error"] is True
    assert result["data"]["succeeded"] == []
    assert result["data"]["unverified"] == [42]
    assert result["data"]["remaining"] is None
    assert result["data"]["results"][0]["verification"] == "unknown"
    assert len(fake_mt5.sent) == 1


def test_mt5_002_bulk_final_listing_can_confirm_an_unverified_close(fake_mt5, connection, monkeypatch):
    # bulk listing, close lookup, close check (fails), bulk final listing (valid, position gone)
    listing_sequence(fake_mt5, monkeypatch, (make_position(42),), (make_position(42),), None, ())

    result = close_all_positions(connection)

    assert result["error"] is False
    assert result["data"]["succeeded"] == [42]
    assert result["data"]["results"][0]["verification"] == "confirmed"


# ---------------------------------------------------------------------------
# MT5-003: checks, send and reservation are atomic per connection
# ---------------------------------------------------------------------------

class SignalingLock:
    """Lock that signals when a second thread starts waiting for it."""

    def __init__(self):
        self._lock = threading.Lock()
        self.contended = threading.Event()

    def __enter__(self):
        if not self._lock.acquire(blocking=False):
            self.contended.set()
            self._lock.acquire()
        return self

    def __exit__(self, *exc):
        self._lock.release()


def run_concurrently(fake_mt5, connection, first, second):
    """
    Run ``first`` until it is inside order_send, then start ``second`` and let
    ``first`` finish only once ``second`` is blocked on the guard lock (or has
    reached order_send itself, which would be the bug).
    """
    lock = SignalingLock()
    connection.risk_guard._opening_lock = lock
    first_in_send = threading.Event()
    second_reached_send = threading.Event()
    calls = []

    def send(request):
        calls.append(request)
        if len(calls) == 1:
            first_in_send.set()
            released = lock.contended.wait(TIMEOUT) or second_reached_send.wait(TIMEOUT)
            assert released, "second request neither waited for the lock nor reached order_send"
        else:
            second_reached_send.set()
        return fake_mt5.result_for(request)

    fake_mt5.results = [send, send]
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(first)
        assert first_in_send.wait(TIMEOUT)
        second_future = pool.submit(second)
        results = first_future.result(TIMEOUT), second_future.result(TIMEOUT)
    assert lock.contended.is_set()
    return results


def buy(connection, volume):
    return lambda: send_order(connection, action=TradeRequestActions.DEAL, order_type="BUY", symbol="EURUSD", volume=volume)


def test_mt5_003_identical_concurrent_requests_send_once(fake_mt5, connection):
    with_risk(connection, max_open_positions=1, duplicate_window_seconds=10)
    fake_mt5.on_send = add_position_on_send(fake_mt5)

    first, second = run_concurrently(fake_mt5, connection, buy(connection, 0.1), buy(connection, 0.1))

    assert first["success"] is True
    assert second["success"] is False
    assert len(fake_mt5.sent) == 1
    assert len(fake_mt5.positions) == 1


def test_mt5_003_different_orders_cannot_share_the_last_slot(fake_mt5, connection):
    # The terminal does not list the first position yet: only the reservation protects the limit.
    with_risk(connection, max_open_positions=1, duplicate_window_seconds=0)

    first, second = run_concurrently(fake_mt5, connection, buy(connection, 0.1), buy(connection, 0.2))

    assert first["success"] is True
    assert second["success"] is False
    assert "recently sent" in second["message"]
    assert len(fake_mt5.sent) == 1


def test_mt5_003_different_orders_cannot_share_the_volume_budget(fake_mt5, connection):
    with_risk(connection, max_total_volume=0.25, duplicate_window_seconds=0)

    first, second = run_concurrently(fake_mt5, connection, buy(connection, 0.2), buy(connection, 0.1))

    assert first["success"] is True
    assert second["success"] is False
    assert len(fake_mt5.sent) == 1


def test_mt5_003_rejected_order_releases_its_reservation(fake_mt5, connection):
    with_risk(connection, max_open_positions=1, duplicate_window_seconds=10)
    fake_mt5.results = [lambda request: fake_mt5.result_for(request, TradeReturnCodes.REQUOTE.value)]

    assert place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1)["status"] == "rejected"
    assert place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1)["error"] is False
    assert len(fake_mt5.sent) == 2


def test_mt5_003_unknown_outcome_keeps_reservation_and_blocks_duplicate(fake_mt5, connection):
    with_risk(connection, max_open_positions=1, duplicate_window_seconds=10)
    fake_mt5.results = [None]

    assert place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1)["status"] == "no_response"
    duplicate = place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1)
    different = place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.2)

    assert "Identical request" in duplicate["message"]
    assert "recently sent" in different["message"]
    assert len(fake_mt5.sent) == 1


def test_mt5_003_partial_fill_reserves_the_filled_volume(fake_mt5, connection):
    with_risk(connection, max_total_volume=0.15, duplicate_window_seconds=0)
    fake_mt5.results = [lambda request: fake_mt5.result_for(request, TradeReturnCodes.DONE_PARTIAL.value, volume=0.04)]

    assert place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1)["status"] == "partial"
    # 0.04 reserved + 0.1 = 0.14 <= 0.15; reserving the requested 0.1 would have blocked it.
    assert place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1)["error"] is False
    assert place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.02)["error"] is True


def test_mt5_003_listed_order_is_not_counted_twice(fake_mt5, connection):
    with_risk(connection, max_open_positions=2, duplicate_window_seconds=0)
    fake_mt5.on_send = add_position_on_send(fake_mt5)

    assert place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1)["error"] is False
    assert place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.2)["error"] is False
    assert place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.3)["error"] is True
    assert len(fake_mt5.sent) == 2


def test_mt5_003_reservation_expires(fake_mt5, connection, monkeypatch):
    monkeypatch.setattr(risk, "RESERVATION_TTL_SECONDS", 0)
    with_risk(connection, max_open_positions=1, duplicate_window_seconds=0)

    assert place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.1)["error"] is False
    assert place_market_order(connection, type="BUY", symbol="EURUSD", volume=0.2)["error"] is False
