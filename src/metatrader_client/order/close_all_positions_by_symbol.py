import MetaTrader5 as mt5
from .close_position import close_position
from ._bulk import run_bulk

def close_all_positions_by_symbol(connection, symbol: str):
    """
    Close all open positions for a given symbol.

    Every item is processed and reported individually, then the terminal is
    queried again to detect items that are still present.

    Args:
        connection: The connection object to the MetaTrader platform.
        symbol: The symbol of the positions to close.

    Returns:
        A dictionary with ``error`` (True if any item failed or remains),
        ``message`` and ``data`` with the per-item results.
    """
    items = mt5.positions_get(symbol=symbol)
    return run_bulk(
        items,
        lambda ticket: close_position(connection, ticket),
        lambda: mt5.positions_get(symbol=symbol),
        verb="Closed",
        noun=f"{symbol} positions",
        error=mt5.last_error,
    )
