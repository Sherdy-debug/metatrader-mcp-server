import MetaTrader5 as mt5
from .cancel_pending_order import cancel_pending_order
from ._bulk import run_bulk

def cancel_all_pending_orders(connection):
    """
    Cancel all pending orders.

    Every item is processed and reported individually, then the terminal is
    queried again to detect items that are still present.

    Args:
        connection: The connection object to the MetaTrader platform.

    Returns:
        A dictionary with ``error`` (True if any item failed or remains),
        ``message`` and ``data`` with the per-item results.
    """
    items = mt5.orders_get()
    return run_bulk(
        items,
        lambda ticket: cancel_pending_order(connection, ticket),
        lambda: mt5.orders_get(),
        verb="Canceled",
        noun="pending orders",
        error=mt5.last_error,
    )
