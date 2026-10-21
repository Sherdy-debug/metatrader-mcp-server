"""
MetaTrader 5 trade return code definitions.

This module contains trade return code definitions for MetaTrader 5 operations.
"""
from enum import Enum


class TradeReturnCodes(Enum):
    """
    Enhanced TradeReturnCodes enumeration with bi-directional mapping capabilities.
    
    This combines the benefits of Python's Enum with dictionary-like lookups:
    - Access numeric values via TradeReturnCodes.DONE, TradeReturnCodes.REQUOTE, etc.
    - Get string representation via TradeReturnCodes.to_string(10004) ("REQUOTE")
    - Get numeric value via TradeReturnCodes.to_code("REQUOTE") (10004)
    - Check if a code or name exists via TradeReturnCodes.exists("REQUOTE") or TradeReturnCodes.exists(10004)
    
    These codes represent the possible return values from trade operations.
    """
    # Values follow the TRADE_RETCODE_* constants of the MetaTrader5 package.
    # https://www.mql5.com/en/docs/constants/errorswarnings/enum_trade_return_codes

    # Success codes
    PLACED = 10008               # Order placed
    PLACE_ORDER = 10008          # Alias kept for backward compatibility
    DONE = 10009                 # Request completed
    DONE_PARTIAL = 10010         # Only part of the request was completed

    # Error codes
    REQUOTE = 10004              # Requote
    REJECT = 10006               # Request rejected
    CANCEL = 10007               # Request canceled by trader
    ERROR = 10011                # Request processing error
    TIMEOUT = 10012              # Request canceled by timeout
    INVALID = 10013              # Invalid request
    INVALID_VOLUME = 10014       # Invalid volume in the request
    INVALID_PRICE = 10015        # Invalid price in the request
    INVALID_STOPS = 10016        # Invalid stops in the request
    TRADE_DISABLED = 10017       # Trade is disabled
    MARKET_CLOSED = 10018        # Market is closed
    NO_MONEY = 10019             # Not enough money
    PRICE_CHANGED = 10020        # Prices changed
    PRICE_OFF = 10021            # No quotes to process the request
    INVALID_EXPIRATION = 10022   # Invalid order expiration date
    ORDER_CHANGED = 10023        # Order state changed
    TOO_MANY_REQUESTS = 10024    # Too frequent requests
    NO_CHANGES = 10025           # No changes in request
    SERVER_DISABLES_AT = 10026   # Autotrading disabled by server
    CLIENT_DISABLES_AT = 10027   # Autotrading disabled by client terminal
    LOCKED = 10028               # Request locked for processing
    FROZEN = 10029               # Order or position frozen
    INVALID_FILL = 10030         # Invalid order filling type
    CONNECTION = 10031           # No connection with the trade server
    ONLY_REAL = 10032            # Operation allowed only for live accounts
    LIMIT_ORDERS = 10033         # Number of pending orders limit reached
    LIMIT_VOLUME = 10034         # Volume of orders and positions limit reached
    INVALID_ORDER = 10035        # Incorrect or prohibited order type
    POSITION_CLOSED = 10036      # Position already closed
    INVALID_CLOSE_VOLUME = 10038 # Close volume exceeds the position volume
    CLOSE_ORDER_EXIST = 10039    # A close order already exists
    LIMIT_POSITIONS = 10040      # Number of open positions limit reached
    REJECT_CANCEL = 10041        # Pending order activation rejected and canceled
    LONG_ONLY = 10042            # Only long positions are allowed
    SHORT_ONLY = 10043           # Only short positions are allowed
    CLOSE_ONLY = 10044           # Only position closing is allowed
    FIFO_CLOSE = 10045           # Position closing allowed only by FIFO rule
    HEDGE_PROHIBITED = 10046     # Opposite positions are prohibited

    @classmethod
    def to_string(cls, code, default=None):
        """
        Convert numeric trade return code to string representation.
        
        Args:
            code: Numeric trade return code
            default: Value to return if code is not found
            
        Returns:
            str: String representation of trade return code or default value
        """
        for return_code in cls:
            if return_code.value == code:
                return return_code.name
        return default or f"UNKNOWN_{code}"
    
    @classmethod
    def to_code(cls, name, default=None):
        """
        Convert string trade return code name to numeric code.
        
        Args:
            name: String representation of trade return code
            default: Value to return if name is not found
            
        Returns:
            int: Numeric code for trade return code or default value
        """
        try:
            return cls[name.upper()].value
        except (KeyError, AttributeError):
            return default
    
    @classmethod
    def exists(cls, key):
        """
        Check if a trade return code or name exists.
        
        Args:
            key: Trade return code (int) or name (str)
            
        Returns:
            bool: True if the trade return code exists
        """
        if isinstance(key, int):
            return any(return_code.value == key for return_code in cls)
        elif isinstance(key, str):
            try:
                cls[key.upper()]
                return True
            except KeyError:
                return False
        return False
