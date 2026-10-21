"""
Risk controls enforced in code before any trade request reaches MetaTrader 5.

The limits do not depend on the judgement of the caller (human or AI agent):
``send_order`` checks them on every request that opens exposure.

Configuration comes from the ``risk`` entry of the client config dictionary
and, for keys not set there, from environment variables:

==============================  ==================================  ========
Config key                      Environment variable                Default
==============================  ==================================  ========
allow_live_trading              MT5_ALLOW_LIVE_TRADING              False
max_volume_per_order            MT5_MAX_VOLUME_PER_ORDER            None
max_total_volume                MT5_MAX_TOTAL_VOLUME                None
max_open_positions              MT5_MAX_OPEN_POSITIONS              None
max_risk_per_trade_pct          MT5_MAX_RISK_PER_TRADE_PCT          None
require_stop_loss               MT5_REQUIRE_STOP_LOSS               False
max_daily_loss                  MT5_MAX_DAILY_LOSS                  None
duplicate_window_seconds        MT5_DUPLICATE_WINDOW_SECONDS        10
==============================  ==================================  ========

``None`` disables a limit. Trading is only allowed on demo accounts unless
``allow_live_trading`` is explicitly enabled.

When a limit needs terminal data that cannot be read (``positions_get``,
``orders_get`` or ``history_deals_get`` returning ``None``), new exposure is
blocked instead of assuming that there is none.

Concurrency: the checks, the request and the reservation of an opening order
run under one lock per :class:`RiskGuard` (one per connection). An order that
was sent keeps a reservation of one slot and its volume until the terminal
lists it or ``RESERVATION_TTL_SECONDS`` pass; a rejected order reserves
nothing; an order with an unknown outcome stays reserved for the full TTL. The
protection covers threads sharing a connection in one process. It does not
coordinate other processes, other connections, manual trading or other robots
using the same account.
"""

import logging
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from typing import Any, Dict, Hashable, Iterator, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

ACCOUNT_TRADE_MODE_DEMO = 0
RESERVATION_TTL_SECONDS = 60.0
DEAL_TYPE_BUY = 0
DEAL_TYPE_SELL = 1

_ENV_PREFIX = "MT5_"
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ValueError(f"Invalid boolean value {value!r}")


@dataclass(frozen=True)
class RiskConfig:
    allow_live_trading: bool = False
    max_volume_per_order: Optional[float] = None
    max_total_volume: Optional[float] = None
    max_open_positions: Optional[int] = None
    max_risk_per_trade_pct: Optional[float] = None
    require_stop_loss: bool = False
    max_daily_loss: Optional[float] = None
    duplicate_window_seconds: float = 10.0

    @classmethod
    def from_sources(cls, config: Optional[Mapping[str, Any]] = None, env: Optional[Mapping[str, str]] = None) -> "RiskConfig":
        """Build the configuration from a mapping, falling back to environment variables."""
        config = dict(config or {})
        env = os.environ if env is None else env
        unknown = set(config) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"Unknown risk settings: {', '.join(sorted(unknown))}")

        values: Dict[str, Any] = {}
        for field in fields(cls):
            raw = config.get(field.name)
            if raw is None:
                env_value = env.get(_ENV_PREFIX + field.name.upper())
                raw = env_value if env_value not in (None, "") else None
            if raw is None:
                continue
            if field.type in (bool, "bool"):
                values[field.name] = _parse_bool(raw)
            elif field.name == "max_open_positions":
                values[field.name] = int(raw)
            else:
                values[field.name] = float(raw)

        for name, value in values.items():
            if not isinstance(value, bool) and value < 0:
                raise ValueError(f"Risk setting {name} must not be negative")
        return cls(**values)


@dataclass
class _Reservation:
    ticket: Optional[int]
    volume: float
    created: float


def _read(mt5, function: str, **kwargs) -> Tuple[Optional[tuple], Optional[str]]:
    """Call a listing function; return (items, None) or (None, error) when the terminal returns None."""
    items = getattr(mt5, function)(**kwargs)
    if items is None:
        code, description = mt5.last_error()
        return None, f"{function} failed (error {code}: {description})"
    return tuple(items), None


class RiskGuard:
    """
    Applies a :class:`RiskConfig` to trade requests.

    Every ``check_*`` method returns ``None`` when the request is allowed and
    an error message otherwise.
    """

    def __init__(self, config: Optional[RiskConfig] = None):
        self.config = config or RiskConfig()
        self._recent: Dict[Hashable, float] = {}
        self._reservations: List[_Reservation] = []
        self._lock = threading.Lock()
        self._opening_lock = threading.Lock()

    @contextmanager
    def opening(self) -> Iterator[None]:
        """
        Serialize the check, send and reservation of orders that open exposure.

        Hold it from the first limit check until :meth:`after_send` has run,
        so concurrent requests on this guard see each other's reservations.
        """
        with self._opening_lock:
            yield

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------
    def check_account(self, mt5) -> Optional[str]:
        """Block trading on non-demo accounts unless live trading is explicitly allowed."""
        if self.config.allow_live_trading:
            return None
        account = mt5.account_info()
        if account is None:
            return "Cannot read account information to verify that the account is a demo account"
        if getattr(account, "trade_mode", None) != ACCOUNT_TRADE_MODE_DEMO:
            return (
                "Trading is blocked: the account is not a demo account. "
                "Set MT5_ALLOW_LIVE_TRADING=true only after validating the setup on a demo account."
            )
        return None

    # ------------------------------------------------------------------
    # Exposure for new orders
    # ------------------------------------------------------------------
    def check_new_order(
        self,
        mt5,
        *,
        symbol: str,
        side: int,
        volume: float,
        entry_price: float,
        stop_loss: Optional[float],
    ) -> Optional[str]:
        """
        Check the limits that apply to an order that opens new exposure.

        Args:
            side: ``mt5.ORDER_TYPE_BUY`` (0) or ``mt5.ORDER_TYPE_SELL`` (1) direction of the order.
        """
        cfg = self.config

        if cfg.max_volume_per_order is not None and volume > cfg.max_volume_per_order + 1e-9:
            return f"Volume {volume} exceeds the per-order limit of {cfg.max_volume_per_order} lots"

        if cfg.max_open_positions is not None or cfg.max_total_volume is not None:
            positions, error = _read(mt5, "positions_get")
            if error:
                return f"Cannot verify exposure limits, new orders are blocked: {error}"
            orders, error = _read(mt5, "orders_get")
            if error:
                return f"Cannot verify exposure limits, new orders are blocked: {error}"
            reserved = self._active_reservations(positions, orders)
            open_count = len(positions) + len(orders) + len(reserved)
            if cfg.max_open_positions is not None and open_count + 1 > cfg.max_open_positions:
                return (
                    f"Opening this order would exceed the limit of {cfg.max_open_positions} "
                    f"open positions and pending orders ({len(positions)} positions, {len(orders)} orders, "
                    f"{len(reserved)} recently sent)"
                )
            if cfg.max_total_volume is not None:
                current = (
                    sum(float(p.volume) for p in positions)
                    + sum(float(o.volume_current) for o in orders)
                    + sum(r.volume for r in reserved)
                )
                if current + volume > cfg.max_total_volume + 1e-9:
                    return (
                        f"Total exposure would be {round(current + volume, 8)} lots, "
                        f"above the limit of {cfg.max_total_volume} lots"
                    )

        error = self.check_stop_loss_risk(
            mt5, symbol=symbol, side=side, volume=volume, entry_price=entry_price, stop_loss=stop_loss
        )
        if error:
            return error

        return self.check_daily_loss(mt5, symbol=symbol)

    def check_stop_loss_risk(
        self,
        mt5,
        *,
        symbol: str,
        side: int,
        volume: float,
        entry_price: float,
        stop_loss: Optional[float],
    ) -> Optional[str]:
        """Require a stop loss when configured and limit the loss it allows."""
        cfg = self.config
        needs_sl = cfg.require_stop_loss or cfg.max_risk_per_trade_pct is not None
        if not stop_loss:
            return "A stop loss is required by the risk settings" if needs_sl else None
        if cfg.max_risk_per_trade_pct is None:
            return None

        account = mt5.account_info()
        if account is None:
            return "Cannot read account information to check the risk per trade"
        loss = mt5.order_calc_profit(side, symbol, volume, entry_price, stop_loss)
        if loss is None:
            return f"Cannot calculate the loss at the stop loss for {symbol}"
        loss = abs(min(float(loss), 0.0))
        equity = float(account.equity)
        if equity <= 0:
            return "Account equity is not positive"
        risk_pct = loss / equity * 100
        if risk_pct > cfg.max_risk_per_trade_pct + 1e-9:
            return (
                f"Loss at stop loss would be {loss:.2f} {account.currency} ({risk_pct:.2f}% of equity), "
                f"above the limit of {cfg.max_risk_per_trade_pct}%"
            )
        return None

    def check_daily_loss(self, mt5, *, symbol: Optional[str] = None) -> Optional[str]:
        """
        Block new exposure once the loss of the current trading day reaches the limit.

        The day starts at 00:00 of the trade server clock. The daily result is the
        realized profit of today's deals (including commission, swap and fees)
        plus the floating profit of open positions.
        """
        limit = self.config.max_daily_loss
        if limit is None:
            return None

        server_now = None
        if symbol:
            tick = mt5.symbol_info_tick(symbol)
            server_now = getattr(tick, "time", None) if tick is not None else None
        if not server_now:
            server_now = int(time.time())
        day_start = int(server_now) - int(server_now) % 86400

        deals = mt5.history_deals_get(
            datetime.fromtimestamp(day_start - 86400, tz=timezone.utc),
            datetime.fromtimestamp(int(server_now) + 86400, tz=timezone.utc),
        )
        if deals is None:
            code, description = mt5.last_error()
            return (
                "Cannot verify the daily loss limit, new orders are blocked: "
                f"history_deals_get failed (error {code}: {description})"
            )
        realized = sum(
            float(d.profit) + float(d.commission) + float(d.swap) + float(getattr(d, "fee", 0.0))
            for d in deals
            if d.time >= day_start and d.type in (DEAL_TYPE_BUY, DEAL_TYPE_SELL)
        )
        positions, error = _read(mt5, "positions_get")
        if error:
            return f"Cannot verify the daily loss limit, new orders are blocked: {error}"
        floating = sum(float(p.profit) + float(p.swap) for p in positions)
        daily = realized + floating
        if daily <= -limit:
            return f"Daily loss limit reached: today's result is {daily:.2f}, limit is -{limit}"
        return None

    # ------------------------------------------------------------------
    # Duplicate requests
    # ------------------------------------------------------------------
    def check_duplicate(self, key: Hashable) -> Optional[str]:
        """Reject a request identical to one sent within the duplicate window."""
        window = self.config.duplicate_window_seconds
        if not window:
            return None
        now = time.monotonic()
        with self._lock:
            self._recent = {k: t for k, t in self._recent.items() if now - t < window}
            if key in self._recent:
                return (
                    f"Identical request sent {now - self._recent[key]:.1f}s ago; "
                    f"repeat it after {window:g}s if it is intended"
                )
        return None

    def record(self, key: Hashable) -> None:
        """Remember a request that was sent, so identical requests are detected."""
        if self.config.duplicate_window_seconds:
            with self._lock:
                self._recent[key] = time.monotonic()

    # ------------------------------------------------------------------
    # Reservations of exposure sent but not yet listed by the terminal
    # ------------------------------------------------------------------
    def after_send(self, response: Dict[str, Any], duplicate_key: Optional[Hashable], volume: float) -> None:
        """
        Apply the reservation policy to the outcome of an opening order.

        - done / placed / partial: remember the request and reserve the executed
          (or placed) volume until the terminal lists the order or position.
        - no_response: remember the request and reserve the requested volume for
          the full TTL, because the order may have been executed.
        - rejected or anything else: nothing is reserved.
        """
        status = response.get("status")
        if status not in ("done", "placed", "partial", "no_response"):
            return
        if duplicate_key is not None:
            self.record(duplicate_key)
        result = response.get("data")
        ticket = getattr(result, "order", None) or None
        if status == "partial":
            volume = float(getattr(result, "volume", 0) or 0) or volume
        with self._lock:
            self._reservations.append(_Reservation(ticket=ticket, volume=float(volume), created=time.monotonic()))

    def _active_reservations(self, positions, orders) -> List[_Reservation]:
        """Drop reservations that expired or that the terminal already lists, return the others."""
        listed = set()
        for item in positions:
            listed.add(getattr(item, "ticket", None))
            listed.add(getattr(item, "identifier", None))
        for item in orders:
            listed.add(getattr(item, "ticket", None))
        now = time.monotonic()
        with self._lock:
            self._reservations = [
                r for r in self._reservations
                if now - r.created < RESERVATION_TTL_SECONDS and (r.ticket is None or r.ticket not in listed)
            ]
            return list(self._reservations)


_default_guard: Optional[RiskGuard] = None
_default_lock = threading.Lock()


def get_risk_guard(connection) -> RiskGuard:
    """Return the guard attached to a connection, or a process-wide guard built from the environment."""
    guard = getattr(connection, "risk_guard", None)
    if isinstance(guard, RiskGuard):
        return guard
    global _default_guard
    with _default_lock:
        if _default_guard is None:
            _default_guard = RiskGuard(RiskConfig.from_sources())
        return _default_guard
