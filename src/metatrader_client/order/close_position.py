import MetaTrader5 as mt5
from typing import Union
from ..types import TradeRequestActions
from .send_order import send_order
from ._result import to_client_response

def close_position(connection, id: Union[str, int]):
    """
    Close a position by its ID.

    After the request the terminal is queried again: if the position is still
    open (for example after a partial fill) the result reports it as an error
    with the remaining volume.

    Args:
        connection: MetaTrader 5 connection object.
        id: The unique identifier of the position to close.

    Returns:
        A dictionary containing an error flag, a message, the order_send
        result, the status and the trade server return code.
    """

    try:
        position_id = int(id)
    except (TypeError, ValueError):
        return {
            "error": True,
            "message": f"Invalid position ID '{id}', it should be a valid integer",
            "data": None,
        }

    positions = mt5.positions_get(ticket=position_id)
    if not positions:
        return {
            "error": True,
            "message": f"Invalid position ID '{id}'",
            "data": None,
        }
    position = positions[0]
    response = send_order(
        connection,
        action=TradeRequestActions.DEAL,
        position=position_id,
        order_type="SELL" if position.type == 0 else "BUY",
        symbol=position.symbol,
        volume=position.volume,
    )
    if response["success"] is False:
        return to_client_response(response)

    data = response["data"]
    result = to_client_response(response, f"Closed position {position_id} at price {getattr(data, 'price', None)} ({response['retcode_name']})")

    remaining = mt5.positions_get(ticket=position_id)
    if remaining:
        result["error"] = True
        result["message"] = (
            f"Position {position_id} is still open with {remaining[0].volume} of {position.volume} lots "
            f"after the close request ({response['retcode_name']})"
        )
    return result
