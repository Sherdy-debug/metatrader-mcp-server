import MetaTrader5 as mt5
from .cancel_pending_order import cancel_pending_order
from ._bulk import run_bulk

def cancel_pending_orders_by_symbol(connection, symbol: str):
    """
    Cancel all pending orders with the given symbol name.

    Every item is processed and reported individually, then the terminal is
    queried again to detect items that are still present.

    Args:
        connection: The connection object to the MetaTrader platform.
        symbol: The symbol name of the orders to be canceled.

    Returns:
        A dictionary with ``error`` (True if any item failed or remains),
        ``message`` and ``data`` with the per-item results.
    """
    items = mt5.orders_get(symbol=symbol)
    return run_bulk(
        items,
        lambda ticket: cancel_pending_order(connection, ticket),
        lambda: mt5.orders_get(symbol=symbol),
        verb="Canceled",
        noun=f"{symbol} pending orders",
        error=mt5.last_error,
    )
