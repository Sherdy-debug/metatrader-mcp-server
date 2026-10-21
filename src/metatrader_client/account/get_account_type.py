from ..exceptions import AccountInfoError, ConnectionError
from .get_account_info import get_account_info

def get_account_type(connection) -> str:
    """
    Get account type (real, demo, or contest).
    Returns:
        str: Account type ("real", "demo", or "contest").
    Raises:
        AccountInfoError: If account type cannot be retrieved.
        ConnectionError: If not connected to terminal.
    """
    account_info = get_account_info(connection)
    trade_mode = account_info["trade_mode"]
    # ACCOUNT_TRADE_MODE_DEMO = 0, ACCOUNT_TRADE_MODE_CONTEST = 1, ACCOUNT_TRADE_MODE_REAL = 2
    if trade_mode == 0:
        return "demo"
    elif trade_mode == 1:
        return "contest"
    elif trade_mode == 2:
        return "real"
    else:
        return f"unknown ({trade_mode})"
