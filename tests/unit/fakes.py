"""
In-memory stand-in for the MetaTrader5 package, used by the unit tests.
"""

from collections import namedtuple
from types import SimpleNamespace
from typing import Callable, List, Optional

from metatrader_client.risk import RiskConfig, RiskGuard

TradeResult = namedtuple(
    "TradeResult",
    "retcode deal order volume price bid ask comment request_id retcode_external request",
)
TradeRequest = namedtuple("TradeRequest", "action symbol volume type price sl tp position order")

PATCHED_MODULES = [
    "metatrader_client.market.get_symbol_price",
    "metatrader_client.order.send_order",
    "metatrader_client.order.place_market_order",
    "metatrader_client.order.modify_pending_order",
    "metatrader_client.order.modify_position",
    "metatrader_client.order.close_position",
    "metatrader_client.order.close_all_positions",
    "metatrader_client.order.close_all_positions_by_symbol",
    "metatrader_client.order.close_all_profitable_positions",
    "metatrader_client.order.close_all_losing_positions",
    "metatrader_client.order.cancel_all_pending_orders",
    "metatrader_client.order.cancel_pending_orders_by_symbol",
]

DONE = 10009


def make_symbol(**overrides):
    values = dict(
        name="EURUSD",
        digits=5,
        point=0.00001,
        trade_tick_size=0.00001,
        volume_min=0.01,
        volume_max=50.0,
        volume_step=0.01,
        trade_stops_level=0,
        filling_mode=1,       # SYMBOL_FILLING_FOK
        trade_exemode=2,      # SYMBOL_TRADE_EXECUTION_MARKET
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def make_tick(bid, ask, time=1_700_000_000):
    return SimpleNamespace(bid=bid, ask=ask, last=0.0, volume=0, time=time)


def make_position(ticket, *, type=0, volume=0.1, symbol="EURUSD", price_open=1.1, sl=0.0, tp=0.0, profit=0.0):
    return SimpleNamespace(
        ticket=ticket, type=type, volume=volume, symbol=symbol,
        price_open=price_open, sl=sl, tp=tp, profit=profit, swap=0.0,
    )


def make_order(ticket, *, type=2, volume=0.1, symbol="EURUSD", price_open=1.09, sl=0.0, tp=0.0,
               type_time=0, time_expiration=0, price_stoplimit=0.0):
    return SimpleNamespace(
        ticket=ticket, type=type, volume_current=volume, symbol=symbol, price_open=price_open,
        sl=sl, tp=tp, type_time=type_time, time_expiration=time_expiration, price_stoplimit=price_stoplimit,
    )


class FakeMT5:
    """Minimal MetaTrader5 replacement with configurable state and order_send results."""

    def __init__(self):
        self.symbols = {"EURUSD": make_symbol()}
        self.ticks = {"EURUSD": make_tick(1.1000, 1.1001)}
        self.account = SimpleNamespace(trade_mode=0, equity=10_000.0, balance=10_000.0, currency="USD")
        self.positions: List[SimpleNamespace] = []
        self.orders: List[SimpleNamespace] = []
        self.deals: List[SimpleNamespace] = []
        self.sent: List[dict] = []
        self.results: List[Optional[TradeResult]] = []
        self.default_retcode = DONE
        self.on_send: Optional[Callable[[dict, Optional[TradeResult]], None]] = None
        self.error = (1, "Success")
        self._next_ticket = 1000

    # Market data
    def symbol_select(self, symbol, enable=True):
        return symbol in self.symbols

    def symbol_info(self, symbol):
        return self.symbols.get(symbol)

    def symbol_info_tick(self, symbol):
        return self.ticks.get(symbol)

    # Account and trading state
    def account_info(self):
        return self.account

    def positions_get(self, ticket=None, symbol=None):
        items = self.positions
        if ticket is not None:
            items = [p for p in items if p.ticket == ticket]
        if symbol is not None:
            items = [p for p in items if p.symbol == symbol]
        return tuple(items)

    def orders_get(self, ticket=None, symbol=None):
        items = self.orders
        if ticket is not None:
            items = [o for o in items if o.ticket == ticket]
        if symbol is not None:
            items = [o for o in items if o.symbol == symbol]
        return tuple(items)

    def history_deals_get(self, *args, ticket=None, **kwargs):
        if ticket is not None:
            return tuple(d for d in self.deals if d.ticket == ticket)
        return tuple(self.deals)

    def order_calc_profit(self, action, symbol, volume, price_open, price_close):
        direction = 1 if action == 0 else -1
        return round((price_close - price_open) * direction * volume * 100_000, 2)

    def last_error(self):
        return self.error

    # Order sending
    def result_for(self, request, retcode=DONE, volume=None, comment=""):
        self._next_ticket += 1
        return TradeResult(
            retcode=retcode,
            deal=self._next_ticket if retcode == DONE else 0,
            order=self._next_ticket,
            volume=request.get("volume", 0.0) if volume is None else volume,
            price=request.get("price", 0.0),
            bid=0.0, ask=0.0, comment=comment, request_id=1, retcode_external=0,
            request=TradeRequest(
                request.get("action"), request.get("symbol"), request.get("volume"), request.get("type"),
                request.get("price"), request.get("sl"), request.get("tp"), request.get("position"), request.get("order"),
            ),
        )

    def order_send(self, request):
        self.sent.append(dict(request))
        if self.results:
            result = self.results.pop(0)
            if callable(result):
                result = result(request)
        else:
            result = self.result_for(request, self.default_retcode)
        if result is not None and result.deal:
            self.deals.append(SimpleNamespace(ticket=result.deal, position_id=result.order))
        if self.on_send:
            self.on_send(request, result)
        return result


def with_risk(connection, **settings):
    connection.risk_guard = RiskGuard(RiskConfig(**settings))
    return connection
