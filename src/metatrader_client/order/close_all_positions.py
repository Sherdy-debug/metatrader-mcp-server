import MetaTrader5 as mt5
from .close_position import close_position
from ._bulk import run_bulk

def close_all_positions(connection):
    """
    Close all open positions.

    Every item is processed and reported individually, then the terminal is
    queried again to detect items that are still present.

    Args:
        connection: The connection object to the MetaTrader platform.

    Returns:
        A dictionary with ``error`` (True if any item failed or remains),
        ``message`` and ``data`` with the per-item results.
    """
    items = mt5.positions_get()
    return run_bulk(
        items,
        lambda ticket: close_position(connection, ticket),
        lambda: mt5.positions_get(),
        verb="Closed",
        noun="positions",
        error=mt5.last_error,
    )
