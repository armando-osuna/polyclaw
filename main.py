"""
main.py — Orchestration loop: scan → validate → risk check → execute → track.

Runs on a SCAN_INTERVAL_MINUTES cycle. Each cycle:
  1. Scans Gamma API for stale high-probability candidates
  2. Runs AI validation (web search + Claude) on each
  3. Applies risk rules
  4. Places limit orders for approved trades
  5. Tracks positions and logs to CSV

Usage:
    python main.py              # full run with DRY_RUN on by default
    python main.py --live       # set DRY_RUN=False (dangerous, be careful)
    python main.py --once       # single pass, no loop
    python main.py --validate   # scan + validate only, no execution
"""

import argparse
import logging
import sys
import time
import signal
from datetime import datetime, timezone

import config
import scanner
import ai_validator
import risk
import executor
import tracker

logger = logging.getLogger(__name__)

# Global flag for graceful shutdown
_running = True


def _setup_logging(level: str = None):
    """Configure logging format and level."""
    level = level or getattr(config, "LOG_LEVEL", "INFO")
    fmt = (
        "%(asctime)s | %(levelname)-7s | %(name)-16s | %(message)s"
    )
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )
    # Also log to file
    fh = logging.FileHandler("trader.log")
    fh.setFormatter(logging.Formatter(fmt))
    logging.getLogger().addHandler(fh)


def _handle_signal(sig, frame):
    """Handle Ctrl+C gracefully."""
    global _running
    logger.info("Received signal %s — shutting down gracefully...", sig)
    _running = False


def _get_usdc_balance() -> float:
    """Fetch USDC balance from CLOB API."""
    try:
        import requests
        url = f"{config.CLOB_API_BASE}/balance/allowance"
        params = {"asset_type": "USDC"}
        resp = requests.get(url, params=params, timeout=10)
        if resp.ok:
            data = resp.json()
            # Balance may be in the response
            balance = float(data.get("balance", 0))
            logger.info("USDC balance: $%.2f", balance)
            return balance
    except Exception as e:
        logger.warning("Failed to fetch USDC balance: %s", e)
    return 0.0


def _cancel_stale_orders():
    """Cancel orders that have been open beyond ORDER_TTL_HOURS."""
    try:
        open_orders = executor.get_open_orders()
        now = time.time()
        for order in open_orders:
            created = order.get("created_at") or order.get("timestamp", 0)
            if isinstance(created, str):
                try:
                    dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    created = dt.timestamp()
                except (ValueError, TypeError):
                    continue
            age_hours = (now - float(created)) / 3600.0
            if age_hours > config.ORDER_TTL_HOURS:
                logger.info("Cancelling stale order %s (age=%.1fh)", order.get("id"), age_hours)
                executor.cancel_order(order.get("id"))
    except Exception as e:
        logger.warning("Failed to cancel stale orders: %s", e)


def _check_hard_exits():
    """Check all tracked positions for HARD_EXIT_PRICE breach."""
    for pos in tracker.get_open_positions():
        entry = float(pos.get("entry_price", 0))
        current = float(pos.get("current_price", 0))
        direction = pos.get("direction", "YES")
        if current > 0 and risk.check_hard_exit(entry, current, direction):
            logger.warning(
                "HARD EXIT triggered for %s — entry=%.4f, current=%.4f",
                pos.get("market_title", "?"), entry, current,
            )
            order_id = pos.get("order_id", "")
            if order_id:
                executor.cancel_order(order_id)


def run_cycle(live_mode: bool = False, validate_only: bool = False):
    """
    Execute one full strategy cycle.

    Args:
        live_mode: If True, sets DRY_RUN to False (submits real orders).
        validate_only: If True, stops after AI validation (no execution).
    """
    logger.info("=" * 60)
    logger.info("CYCLE START — %s", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"))
    logger.info("DRY_RUN=%s | live_mode=%s | validate_only=%s",
                 config.DRY_RUN, live_mode, validate_only)
    logger.info("=" * 60)

    # Override DRY_RUN if --live flag is set
    if live_mode:
        logger.warning("⚠️  LIVE MODE — orders WILL be submitted to the exchange!")
        config.DRY_RUN = False

    # Step 1: Scan for candidates
    logger.info("STEP 1 — Scanning for stale high-probability markets...")
    candidates = scanner.scan()
    if not candidates:
        logger.info("No candidates found this cycle.")
        return

    # Step 2: AI validation
    logger.info("STEP 2 — AI validation (%d candidates)...", len(candidates))
    approved = ai_validator.validate_batch(candidates)
    if not approved:
        logger.info("No markets approved by AI validation.")
        return

    logger.info("AI approved %d markets for trading.", len(approved))

    if validate_only:
        logger.info("Validate-only mode — stopping after AI validation.")
        for market, result in approved:
            logger.info(
                "  ✓ %s | confidence=%d | direction=%s | reasoning=%s",
                market.get("title", "?")[:50],
                result["confidence"],
                result["direction"],
                result["reasoning"][:100],
            )
        return

    # Step 3: Get balance and open positions
    usdc_balance = _get_usdc_balance()
    open_pos_count = tracker.count_open_positions()

    logger.info("Balance: $%.2f | Open positions: %d/%d",
                 usdc_balance, open_pos_count, config.MAX_OPEN_POSITIONS)

    if usdc_balance < config.MIN_LIQUIDITY_USDC:
        logger.warning("Balance below minimum liquidity — skipping execution.")

    # Step 4: Execute trades
    logger.info("STEP 4 — Executing trades...")
    for market, result in approved:
        if not _running:
            break

        direction = result.get("direction", market.get("_direction", "YES"))
        per_trade_size = usdc_balance * config.PER_TRADE_PERCENT

        logger.info(
            "Placing order: %s | dir=%s | size=$%.2f",
            market.get("title", "?")[:50], direction, per_trade_size,
        )

        order_result = executor.place_order(
            market=market,
            direction=direction,
            size_usdc=per_trade_size,
            usdc_balance=usdc_balance,
            open_positions_count=tracker.count_open_positions(),
        )

        if order_result:
            tracker.record_order(order_result, market)

    # Step 5: Track and log
    logger.info("STEP 5 — Updating positions...")
    tracker.update_positions()

    # Cancel stale orders
    _cancel_stale_orders()

    # Check hard exits
    _check_hard_exits()

    summary = tracker.summary()
    logger.info(
        "CYCLE END — positions=%d, total PnL=$%.2f, total size=$%.2f",
        summary["open_positions"],
        summary["total_pnl_usd"],
        summary["total_size_usdc"],
    )


def run_loop(live_mode: bool = False, validate_only: bool = False):
    """Run the strategy loop indefinitely on SCAN_INTERVAL_MINUTES."""
    global _running

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    logger.info(
        "Starting main loop — interval=%d min, live=%s, validate_only=%s",
        config.SCAN_INTERVAL_MINUTES, live_mode, validate_only,
    )

    # Start WebSocket fill monitor in background
    try:
        executor.monitor_fills_background()
    except Exception as e:
        logger.warning("Could not start fill monitor: %s", e)

    while _running:
        cycle_start = time.time()
        try:
            run_cycle(live_mode=live_mode, validate_only=validate_only)
        except Exception as e:
            logger.error("Cycle failed with exception", exc_info=e)

        if not _running:
            break

        elapsed = time.time() - cycle_start
        sleep_time = max(0, config.SCAN_INTERVAL_MINUTES * 60 - elapsed)
        logger.info("Sleeping for %.1f minutes...", sleep_time / 60.0)

        # Sleep in small increments so we can catch shutdown signals
        for _ in range(int(sleep_time)):
            if not _running:
                break
            time.sleep(1)

    logger.info("Shutdown complete.")


def main():
    parser = argparse.ArgumentParser(
        description="Polymarket Stale High-Probability Hunter",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python main.py              # DRY_RUN loop\n"
            "  python main.py --once       # single pass, DRY_RUN\n"
            "  python main.py --validate   # scan + AI validate only\n"
            "  python main.py --live       # ⚠️  real money!\n"
        ),
    )
    parser.add_argument(
        "--live", action="store_true",
        help="⚠️  Disable DRY_RUN and submit real orders",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run a single cycle and exit (no loop)",
    )
    parser.add_argument(
        "--validate", action="store_true",
        help="Scan + validate only, skip execution",
    )
    args = parser.parse_args()

    _setup_logging()

    logger.info("=" * 60)
    logger.info("POLYMARKET TRADER — Stale High-Probability Hunter")
    logger.info("=" * 60)

    # Validate credentials are set (warn but don't block for DRY_RUN)
    if not config.POLYMARKET_PRIVATE_KEY:
        logger.warning("POLYMARKET_PRIVATE_KEY not set — check .env file")

    if not config.ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not set — AI validation will fail")

    if args.live:
        confirm = input(
            "⚠️  LIVE MODE — real orders will be submitted. "
            "Type 'yes' to confirm: "
        )
        if confirm.strip().lower() != "yes":
            logger.info("Live mode cancelled.")
            sys.exit(0)

    if args.once:
        run_cycle(live_mode=args.live, validate_only=args.validate)
    else:
        run_loop(live_mode=args.live, validate_only=args.validate)


if __name__ == "__main__":
    main()