"""
risk.py — Hard risk management rules.

All enforcements happen here. The DRY_RUN flag is checked on every
order submission path — no order reaches the exchange when it's True.
"""

import logging
from typing import Any

import config

logger = logging.getLogger(__name__)


class RiskViolation(Exception):
    """Raised when a risk check prevents an action."""
    pass


def require_dry_run():
    """Raise if DRY_RUN is True — used to wrap order submission."""
    if config.DRY_RUN:
        raise RiskViolation(
            "DRY_RUN is enabled. Set config.DRY_RUN = False to submit real orders. "
            "All orders are currently being logged only."
        )


def check_max_positions(open_positions_count: int):
    """Raise if we'd exceed MAX_OPEN_POSITIONS."""
    if open_positions_count >= config.MAX_OPEN_POSITIONS:
        raise RiskViolation(
            f"Position limit reached: {open_positions_count} >= {config.MAX_OPEN_POSITIONS}. "
            f"Skipping new trade."
        )


def check_trade_size(usdc_balance: float, proposed_size: float):
    """Raise if proposed_size exceeds per-trade % of balance."""
    max_trade = usdc_balance * config.PER_TRADE_PERCENT
    if proposed_size > max_trade:
        raise RiskViolation(
            f"Trade size ${proposed_size:.2f} exceeds ${max_trade:.2f} "
            f"({config.PER_TRADE_PERCENT*100:.0f}% of ${usdc_balance:.2f} balance)."
        )


def check_hard_exit(entry_price: float, current_price: float, direction: str) -> bool:
    """
    Check if a position should be exited.
    Returns True if HARD_EXIT_PRICE threshold breached.

    For YES positions: exit if current price < HARD_EXIT_PRICE.
    For NO positions: exit if current price > (1 - HARD_EXIT_PRICE) [NO at $0.20 = exit].
    """
    if direction == "YES":
        if current_price < config.HARD_EXIT_PRICE:
            logger.warning(
                "HARD EXIT: YES position entered at %.4f now at %.4f (threshold: %.2f)",
                entry_price, current_price, config.HARD_EXIT_PRICE,
            )
            return True
    elif direction == "NO":
        no_price = 1.0 - current_price
        if no_price < config.HARD_EXIT_PRICE:
            logger.warning(
                "HARD EXIT: NO position (implied YES=%.4f) entered at %.4f now at %.4f",
                current_price, entry_price, current_price,
            )
            return True
    return False


def check_resolve_hours(hours_until_resolve: float):
    """Raise if market resolves too soon."""
    if hours_until_resolve < config.MIN_RESOLVE_HOURS:
        raise RiskViolation(
            f"Market resolves in {hours_until_resolve:.1f}h, minimum is "
            f"{config.MIN_RESOLVE_HOURS}h. Too volatile, skipping."
        )


def validate_trade(
    market: dict,
    direction: str,
    proposed_size: float,
    usdc_balance: float,
    open_positions_count: int,
) -> dict:
    """
    Run all risk checks for a proposed trade.

    Returns a dict with:
      - "approved": bool
      - "reason": str (if not approved)
      - "adjusted_size": float (size after risk adjustments)
    """
    result = {
        "approved": True,
        "reason": "",
        "adjusted_size": proposed_size,
    }

    # Check position limit
    try:
        check_max_positions(open_positions_count)
    except RiskViolation as e:
        result["approved"] = False
        result["reason"] = str(e)
        logger.warning("Risk block: %s", e)
        return result

    # Check trade size
    try:
        check_trade_size(usdc_balance, proposed_size)
    except RiskViolation as e:
        # Don't fail — cap the size instead
        max_trade = usdc_balance * config.PER_TRADE_PERCENT
        logger.warning("Risk cap: proposed $%.2f capped to $%.2f", proposed_size, max_trade)
        result["adjusted_size"] = max_trade

    # Check resolve time
    end_date = market.get("endDate", "")
    if end_date:
        try:
            from datetime import datetime, timezone
            end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            end_dt = end_dt.replace(tzinfo=None)
            from datetime import datetime as dt_now
            hours_to_resolve = (end_dt - dt_now.utcnow()).total_seconds() / 3600.0
            check_resolve_hours(hours_to_resolve)
        except (ValueError, TypeError) as e:
            logger.warning("Could not parse end_date '%s': %s", end_date, e)

    logger.info(
        "Trade validated: direction=%s, size=$%.2f, positions=%d/%d",
        direction, result["adjusted_size"],
        open_positions_count + 1, config.MAX_OPEN_POSITIONS,
    )

    return result


def dry_run_log(action: str, details: dict):
    """
    Central logging point for DRY_RUN mode.
    Call this instead of submitting any order when DRY_RUN is True.
    """
    logger.info("=" * 60)
    logger.info("DRY RUN — %s", action.upper())
    for k, v in details.items():
        logger.info("  %s: %s", k, v)
    logger.info("=" * 60)