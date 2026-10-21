"""
Symbol-aware validation helpers for trade requests.

All functions are pure: they receive the symbol information returned by
``mt5.symbol_info`` (or any object exposing the same attributes) and never talk
to the terminal, so they can be tested without MetaTrader 5.
"""

import math
from typing import Optional, Tuple

from ..types import OrderType

# MetaTrader5 constants, duplicated here so the helpers stay independent from the package.
ORDER_FILLING_FOK = 0
ORDER_FILLING_IOC = 1
ORDER_FILLING_RETURN = 2

SYMBOL_FILLING_FOK = 1
SYMBOL_FILLING_IOC = 2

SYMBOL_TRADE_EXECUTION_REQUEST = 0
SYMBOL_TRADE_EXECUTION_INSTANT = 1
SYMBOL_TRADE_EXECUTION_MARKET = 2
SYMBOL_TRADE_EXECUTION_EXCHANGE = 3

BUY_SIDE = (OrderType.BUY.value, OrderType.BUY_LIMIT.value, OrderType.BUY_STOP.value, OrderType.BUY_STOP_LIMIT.value)
SELL_SIDE = (OrderType.SELL.value, OrderType.SELL_LIMIT.value, OrderType.SELL_STOP.value, OrderType.SELL_STOP_LIMIT.value)


def is_buy(order_type: int) -> bool:
    return order_type in BUY_SIDE


def _point(symbol_info) -> float:
    point = getattr(symbol_info, "point", 0) or 0
    if point > 0:
        return point
    return 10 ** -int(getattr(symbol_info, "digits", 5) or 5)


def _tick_size(symbol_info) -> float:
    tick_size = getattr(symbol_info, "trade_tick_size", 0) or 0
    return tick_size if tick_size > 0 else _point(symbol_info)


def _decimals(step: float) -> int:
    text = f"{step:.10f}".rstrip("0")
    return len(text.split(".")[1]) if "." in text else 0


def select_filling_mode(symbol_info, *, pending: bool) -> Tuple[Optional[int], Optional[str]]:
    """
    Choose the order filling policy allowed for a symbol.

    Rules (https://www.mql5.com/en/docs/constants/tradingconstants/orderproperties):
    - Pending orders always use RETURN, regardless of the execution mode.
    - Request and Instant execution accept FOK for market orders.
    - Market execution accepts FOK or IOC only when enabled for the symbol; RETURN is not allowed.
    - Exchange execution accepts FOK or IOC when enabled, and RETURN always.

    Returns:
        (filling, None) when a policy is found, (None, error message) otherwise.
    """
    if pending:
        return ORDER_FILLING_RETURN, None

    mask = int(getattr(symbol_info, "filling_mode", 0) or 0)
    exemode = int(getattr(symbol_info, "trade_exemode", SYMBOL_TRADE_EXECUTION_MARKET))

    if exemode in (SYMBOL_TRADE_EXECUTION_REQUEST, SYMBOL_TRADE_EXECUTION_INSTANT):
        return ORDER_FILLING_FOK, None
    if mask & SYMBOL_FILLING_FOK:
        return ORDER_FILLING_FOK, None
    if mask & SYMBOL_FILLING_IOC:
        return ORDER_FILLING_IOC, None
    if exemode == SYMBOL_TRADE_EXECUTION_EXCHANGE:
        return ORDER_FILLING_RETURN, None
    return None, (
        f"No supported filling mode for {getattr(symbol_info, 'name', 'symbol')} "
        f"(execution mode {exemode}, filling mask {mask})"
    )


def validate_volume(symbol_info, volume) -> Tuple[Optional[float], Optional[str]]:
    """
    Check a volume against the symbol's minimum, maximum and step.

    Returns:
        (normalized volume, None) or (None, error message).
    """
    try:
        volume = float(volume)
    except (TypeError, ValueError):
        return None, f"Invalid volume {volume!r}"
    if not math.isfinite(volume) or volume <= 0:
        return None, f"Invalid volume {volume}, must be greater than 0"

    vmin = float(getattr(symbol_info, "volume_min", 0) or 0)
    vmax = float(getattr(symbol_info, "volume_max", 0) or 0)
    step = float(getattr(symbol_info, "volume_step", 0) or 0)

    if vmin > 0 and volume < vmin - 1e-9:
        return None, f"Volume {volume} is below the symbol minimum {vmin}"
    if vmax > 0 and volume > vmax + 1e-9:
        return None, f"Volume {volume} is above the symbol maximum {vmax}"
    if step > 0:
        steps = round(volume / step)
        if abs(steps * step - volume) > step * 1e-6:
            return None, f"Volume {volume} is not a multiple of the symbol volume step {step}"
        volume = round(steps * step, _decimals(step))
    return volume, None


def normalize_price(symbol_info, value, label: str = "Price") -> Tuple[Optional[float], Optional[str]]:
    """
    Check that a price matches the symbol precision (digits and tick size).

    The price is rejected instead of silently rounded, so the caller always
    trades the level it asked for. The error message includes the nearest
    valid value.
    """
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None, f"{label} {value!r} is not a number"
    if not math.isfinite(value) or value <= 0:
        return None, f"{label} {value} must be greater than 0"

    digits = int(getattr(symbol_info, "digits", 5) or 0)
    tick_size = _tick_size(symbol_info)
    nearest = round(round(value / tick_size) * tick_size, digits)
    if abs(nearest - value) > tick_size * 1e-3:
        return None, (
            f"{label} {value} does not match the symbol precision "
            f"({digits} digits, tick size {tick_size}); nearest valid value is {nearest}"
        )
    return nearest, None


def min_stop_distance(symbol_info) -> float:
    """Minimal distance in price units between the current/order price and SL, TP or pending price."""
    return float(getattr(symbol_info, "trade_stops_level", 0) or 0) * _point(symbol_info)


def check_stops(
    symbol_info,
    order_type: int,
    *,
    reference_price: float,
    stop_loss: Optional[float],
    take_profit: Optional[float],
) -> Optional[str]:
    """
    Check SL/TP direction and the symbol minimal stop distance.

    ``reference_price`` is the price the stops are measured from: the order
    price for pending orders, the closing price (bid for buys, ask for sells)
    for market positions. A value of 0 or None means "no level".

    Returns:
        None if valid, otherwise an error message.
    """
    distance = min_stop_distance(symbol_info)
    eps = _point(symbol_info) * 1e-3
    buy = is_buy(order_type)

    if stop_loss:
        gap = (reference_price - stop_loss) if buy else (stop_loss - reference_price)
        if gap <= eps:
            side = "below" if buy else "above"
            return f"Stop loss {stop_loss} must be {side} {reference_price}"
        if gap < distance - eps:
            return f"Stop loss {stop_loss} is closer than the minimum stop distance {distance} from {reference_price}"

    if take_profit:
        gap = (take_profit - reference_price) if buy else (reference_price - take_profit)
        if gap <= eps:
            side = "above" if buy else "below"
            return f"Take profit {take_profit} must be {side} {reference_price}"
        if gap < distance - eps:
            return f"Take profit {take_profit} is closer than the minimum stop distance {distance} from {reference_price}"

    return None


def check_pending_price(symbol_info, order_type: int, *, price: float, bid: float, ask: float) -> Optional[str]:
    """
    Check that a pending order price is on the correct side of the market and
    respects the symbol minimal stop distance.
    """
    distance = min_stop_distance(symbol_info)
    eps = _point(symbol_info) * 1e-3

    if order_type == OrderType.BUY_LIMIT.value:
        gap, rule = ask - price, f"below the current ask {ask}"
    elif order_type == OrderType.SELL_LIMIT.value:
        gap, rule = price - bid, f"above the current bid {bid}"
    elif order_type == OrderType.BUY_STOP.value:
        gap, rule = price - ask, f"above the current ask {ask}"
    elif order_type == OrderType.SELL_STOP.value:
        gap, rule = bid - price, f"below the current bid {bid}"
    else:
        return f"Unsupported pending order type {OrderType.to_string(order_type)}"

    if gap <= eps:
        return f"Invalid price {price} for {OrderType.to_string(order_type)}, must be {rule}"
    if gap < distance - eps:
        return f"Price {price} is closer than the minimum stop distance {distance} to the market"
    return None
