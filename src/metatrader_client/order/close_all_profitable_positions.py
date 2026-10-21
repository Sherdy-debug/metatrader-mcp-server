import MetaTrader5 as mt5
from .close_position import close_position
from ._bulk import run_bulk

def close_all_profitable_positions(connection):
    """
    Close all open positions that are currently profitable (profit >= 0).

    Every item is processed and reported individually, then the terminal is
    queried again to detect items that are still present.

    Args:
        connection: The connection object to the MetaTrader platform.

    Returns:
        A dictionary with ``error`` (True if any item failed or remains),
        ``message`` and ``data`` with the per-item results.
    """
    items = mt5.positions_get()
    if items is not None:
        items = [item for item in items if item.profit >= 0]
    return run_bulk(
        items,
        lambda ticket: close_position(connection, ticket),
        lambda: mt5.positions_get(),
        verb="Closed",
        noun="profitable positions",
        error=mt5.last_error,
    )
