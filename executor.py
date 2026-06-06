"""
executor.py — Order placement, cancellation, and fill monitoring.

Uses py_clob_client for EIP-712 signing and HMAC auth.
Places limit orders at mid or 1 tick below ask for maker rebates.
Cancels unfilled orders after ORDER_TTL_HOURS.
Monitors fills via WebSocket.
"""

import json
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable

import config
import risk

logger = logging.getLogger(__name__)


# Lazy import of py_clob_client — it's a heavy dependency
def _get_clob_client():
    """Build and return an authenticated CLOB client."""
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import Credentials

    creds = Credentials(
        api_key=config.POLYMARKET_API_KEY or None,
        secret=config.POLYMARKET_SECRET or None,
        passphrase=config.POLYMARKET_PASSPHRASE or None,
    )

    client = ClobClient(
        host=config.CLOB_API_BASE,
        chain_id=config.CLOB_API_CHAIN_ID,
        private_key=config.POLYMARKET_PRIVATE_KEY,
        creds=creds,
    )

    # Derive credentials if not directly provided
    if not config.POLYMARKET_API_KEY:
        client.derive_credentials()
        logger.info("Derived CLOB API credentials from private key")

    return client


def _get_mid_price(orderbook: dict) -> float:
    """Calculate mid price from orderbook best bid/ask."""
    try:
        best_bid = float(orderbook["bids"][0][0]) if orderbook.get("bids") else 0.0
        best_ask = float(orderbook["asks"][0][0]) if orderbook.get("asks") else 1.0
        return (best_bid + best_ask) / 2.0
    except (IndexError, ValueError, TypeError, KeyError):
        return 0.0


def _price_one_tick_below_ask(orderbook: dict, tick_size: float = 0.0001) -> float:
    """Return 1 tick below the best ask for maker rebates."""
    try:
        best_ask = float(orderbook["asks"][0][0]) if orderbook.get("asks") else 1.0
        return round(best_ask - tick_size, 4)
    except (IndexError, ValueError, TypeError, KeyError):
        return 0.0


def place_order(
    market: dict,
    direction: str,
    size_usdc: float,
    usdc_balance: float,
    open_positions_count: int,
) -> dict | None:
    """
    Place a limit order for the given market.

    Returns order result dict on success, None if blocked by risk or DRY_RUN.
    """
    # Get token IDs from scanner output — scanner sets _yes_token / _no_token
    yes_token = market.get("_yes_token", "")
    no_token = market.get("_no_token", "")
    if not yes_token or not no_token:
        logger.error("Missing token IDs for market '%s'", market.get("_title", "?"))
        return None

    # Choose the correct token based on direction
    token_id = yes_token if direction == "YES" else no_token

    # Run risk validation
    rv = risk.validate_trade(market, direction, size_usdc, usdc_balance, open_positions_count)
    if not rv["approved"]:
        logger.info("Trade rejected by risk rules: %s", rv["reason"])
        return None

    adjusted_size = rv["adjusted_size"]
    orderbook = market.get("_orderbook", {})

    # Determine price: mid for aggressive, 1-tick-below-ask for maker
    mid = _get_mid_price(orderbook)
    maker_price = _price_one_tick_below_ask(orderbook)

    # Use the more conservative of the two (lower = better for buyer, worse for seller)
    if direction == "YES":
        price = min(mid, maker_price) if mid and maker_price else (mid or maker_price)
    else:
        # For NO, we want the highest price (buying NO at high price = selling YES)
        price = max(mid, maker_price) if mid and maker_price else (mid or maker_price)
    price = max(0.01, min(0.99, round(price, 4)))  # clamp to valid range

    logger.info(
        "Order price: mid=%.4f, maker=%.4f, chosen=%.4f for %s",
        mid, maker_price, price, direction,
    )

    # DRY_RUN: log and bail
    if config.DRY_RUN:
        risk.dry_run_log("PLACE ORDER", {
            "market": market.get("_title", "?"),
            "token_id": token_id,
            "direction": direction,
            "size_usdc": adjusted_size,
            "price": price,
            "conditionId": market.get("conditionId", ""),
        })
        return {
            "dry_run": True,
            "market_title": market.get("_title", "?"),
            "direction": direction,
            "size": adjusted_size,
            "price": price,
            "status": "dry_run",
        }

    # Place real order
    try:
        client = _get_clob_client()
        from py_clob_client.clob_types import OrderArgs, LimitOrderArgs

        side = "BUY"  # always BUY the chosen outcome token
        token_id_to_buy = token_id  # Already selected YES or NO based on direction

        # Convert size to integer atomic units (6 decimals for USDC on Polymarket)
        size_atomic = int(Decimal(str(adjusted_size)) * Decimal(10**6))
        price_atomic = int(Decimal(str(price)) * Decimal(10**6))

        order_args = LimitOrderArgs(
            token_id=token_id_to_buy,
            price=price_atomic,
            size=size_atomic,
            side=side,
        )

        signed_order = client.create_order(order_args)
        resp = client.post_order(signed_order)

        logger.info("Order placed: id=%s, market=%s", resp.get("id"), market.get("_title", "?")[:50])

        return {
            "dry_run": False,
            "order_id": resp.get("id"),
            "market_title": market.get("_title", "?"),
            "direction": direction,
            "size": adjusted_size,
            "price": price,
            "status": "placed",
            "response": resp,
        }

    except Exception as e:
        logger.error("Order placement failed for '%s': %s", market.get("_title", "?"), e)
        return None


def cancel_order(order_id: str) -> bool:
    """Cancel a specific order by ID. Returns True on success."""
    if config.DRY_RUN:
        risk.dry_run_log("CANCEL ORDER", {"order_id": order_id})
        return True

    try:
        client = _get_clob_client()
        resp = client.cancel(order_id=order_id)
        logger.info("Cancelled order %s: %s", order_id, resp)
        return True
    except Exception as e:
        logger.error("Failed to cancel order %s: %s", order_id, e)
        return False


def cancel_all_orders(token_ids: list[str]) -> bool:
    """Cancel all orders for given token IDs."""
    if config.DRY_RUN:
        risk.dry_run_log("CANCEL ALL", {"token_ids": token_ids})
        return True

    try:
        client = _get_clob_client()
        resp = client.cancel_all(token_ids=token_ids)
        logger.info("Cancelled all orders for %d tokens: %s", len(token_ids), resp)
        return True
    except Exception as e:
        logger.error("Failed to cancel all: %s", e)
        return False


def get_open_orders() -> list[dict]:
    """Fetch all open orders from CLOB API."""
    try:
        client = _get_clob_client()
        orders = client.get_orders()
        return orders
    except Exception as e:
        logger.error("Failed to fetch open orders: %s", e)
        return []


# ─── WebSocket Fill Monitor ───────────────────────────────────────────────────

def _fill_callback(order: dict):
    """Default callback when a fill is detected."""
    logger.info("FILL DETECTED: %s", json.dumps(order, default=str))


def monitor_fills(callback: Callable = _fill_callback):
    """
    Connect to CLOB WebSocket and stream fill events.
    This is a blocking call — run in a separate thread.

    Uses the official SDK's WebSocket support.
    """
    try:
        from py_clob_client.client import ClobClient

        client = _get_clob_client()

        # The SDK supports WebSocket via client.create_or_derived_api_creds
        # then connecting:
        ws_url = f"wss://ws-clob.polymarket.com/ws"

        import websocket

        def on_message(ws, message):
            try:
                data = json.loads(message)
                if data.get("type") == "fill":
                    logger.info("WebSocket fill event: %s", json.dumps(data, default=str))
                    callback(data)
            except (json.JSONDecodeError, Exception) as e:
                logger.warning("WebSocket message error: %s", e)

        def on_error(ws, error):
            logger.error("WebSocket error: %s", error)

        def on_close(ws, close_code, close_reason):
            logger.info("WebSocket closed: %s %s", close_code, close_reason)

        def on_open(ws):
            logger.info("WebSocket connected — subscribing to fills")
            # Subscribe to user's fill events (requires auth)
            auth_msg = {
                "type": "subscribe",
                "channel": "user",
                "account": config.POLYMARKET_FUNDER_ADDRESS,
            }
            ws.send(json.dumps(auth_msg))

        ws = websocket.WebSocketApp(
            ws_url,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open,
        )
        logger.info("Starting WebSocket fill monitor...")
        ws.run_forever()

    except ImportError as e:
        logger.error("WebSocket dependencies not installed: %s", e)
        logger.error("Run: pip install websocket-client")
    except Exception as e:
        logger.error("WebSocket monitor failed: %s", e)


def monitor_fills_background():
    """Start fill monitor in a daemon thread."""
    import threading
    t = threading.Thread(target=monitor_fills, daemon=True)
    t.start()
    logger.info("Fill monitor started in background thread")
    return t