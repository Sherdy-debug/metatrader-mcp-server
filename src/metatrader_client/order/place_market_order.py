import MetaTrader5 as mt5
from typing import Union, Optional
from ..types import TradeRequestActions
from .send_order import send_order
from ._result import to_client_response, STATUS_PARTIAL

def place_market_order(
    connection,
    *,
    type: str,
    symbol: str,
    volume: Union[float, int],
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
):
    """
    Places a market order for a specified financial instrument.

    This function sends a market order request to the MetaTrader 5 platform. It supports
    BUY and SELL order types and returns the result of the operation.

    Args:
        connection: The MetaTrader 5 connection object.
        type: The type of market order, either "BUY" or "SELL".
        symbol: The trading instrument symbol (e.g., "EURUSD").
        volume: The volume of the trade operation in lots.
        stop_loss: Optional stop loss price. None or 0 means no stop loss.
        take_profit: Optional take profit price. None or 0 means no take profit.

    Returns:
        dict: ``error`` flag, ``message``, ``data`` (order_send result), ``status``
              ('done', 'partial', 'rejected', 'no_response', ...), ``retcode`` and,
              when the deal is found in the terminal history, ``position_id``.
    """

    type = str(type).upper()

    if type not in ["BUY", "SELL"]:
        return { "error": True, "message": f"Invalid type, should be BUY or SELL.", "data": None }

    response = send_order(
        connection,
        action=TradeRequestActions.DEAL,
        order_type=type,
        symbol=symbol,
        volume=volume,
        stop_loss=stop_loss,
        take_profit=take_profit,
    )

    if response["success"] is False:
        return to_client_response(response)

    data = response["data"]
    filled = "Partially filled" if response["status"] == STATUS_PARTIAL else "Filled"
    result = to_client_response(
        response,
        f"{filled} {type} {symbol} {data.volume} of {response['request']['volume']} LOT at {data.price} "
        f"(order {data.order}, deal {data.deal}, {response['retcode_name']})",
    )

    # Reconcile with the terminal: the deal must exist in the account history.
    deals = mt5.history_deals_get(ticket=data.deal) if data.deal else None
    if deals:
        result["position_id"] = deals[0].position_id
    else:
        result["warning"] = "The deal was not found in the terminal history yet; verify the open positions."
    return result
