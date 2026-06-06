"""
tracker.py — Open position tracking, P&L calculation, and CSV logging.

Polls the Polymarket Data API (or local state) for open positions
and running P&L. Logs to CSV with timestamp, market title, entry price,
current price, and size.
"""

import csv
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import config

logger = logging.getLogger(__name__)


# In-memory position store (persisted to CSV)
_positions: dict[str, dict] = {}  # conditionId -> position info


def record_order(order_result: dict, market: dict):
    """
    Record a newly placed order as a tracked position.
    """
    if not order_result:
        return

    cid = market.get("conditionId", "")
    if not cid:
        return

    _positions[cid] = {
        "conditionId": cid,
        "market_title": market.get("_title", "?"),
        "direction": order_result.get("direction", "?"),
        "entry_price": order_result.get("price", 0.0),
        "size": order_result.get("size", 0.0),
        "order_id": order_result.get("order_id", ""),
        "timestamp": datetime.utcnow().isoformat(),
        "status": order_result.get("status", "placed"),
    }

    logger.info(
        "Position recorded: %s — %s at $%.4f for $%.2f",
        _positions[cid]["market_title"][:50],
        _positions[cid]["direction"],
        _positions[cid]["entry_price"],
        _positions[cid]["size"],
    )
    _append_csv(_positions[cid], current_price=None)


def _fetch_current_prices() -> dict[str, float]:
    """
    Fetch current prices for tracked positions from CLOB API.
    Returns dict of conditionId -> current_price.
    """
    prices = {}
    try:
        import requests
        for cid, pos in _positions.items():
            token_id = _get_token_id_for_position(pos)
            if token_id:
                url = f"{config.CLOB_API_BASE}/orderbook"
                params = {"token_id": token_id}
                resp = requests.get(url, params=params, timeout=10)
                if resp.ok:
                    ob = resp.json()
                    bids = ob.get("bids", [])
                    if bids:
                        prices[cid] = float(bids[0][0])  # best bid
    except Exception as e:
        logger.warning("Failed to fetch current prices: %s", e)
    return prices


def _get_token_id_for_position(pos: dict) -> str:
    """Get the token ID for a position. Placeholder — should be stored when recording."""
    return pos.get("conditionId", "")


def compute_pnl(pos: dict, current_price: float | None = None) -> dict:
    """
    Compute P&L for a single position.

    Returns dict with pnl_usd, pnl_pct, current_price, etc.
    """
    entry = float(pos.get("entry_price", 0))
    size = float(pos.get("size", 0))
    direction = pos.get("direction", "YES")

    if current_price is None:
        current_price = entry  # no update available

    if direction == "YES":
        # Profit = (current_price - entry) * size / entry  (rough)
        if entry > 0:
            pnl_pct = (current_price - entry) / entry * 100.0
            pnl_usd = (current_price - entry) * size / entry if entry > 0 else 0.0
        else:
            pnl_pct = 0.0
            pnl_usd = 0.0
    else:
        # For NO: profit if YES price drops (NO price rises toward $1)
        no_entry = 1.0 - entry
        no_current = 1.0 - current_price
        if no_entry > 0:
            pnl_pct = (no_current - no_entry) / no_entry * 100.0
            pnl_usd = (no_current - no_entry) * size / no_entry if no_entry > 0 else 0.0
        else:
            pnl_pct = 0.0
            pnl_usd = 0.0

    return {
        "pnl_usd": round(pnl_usd, 2),
        "pnl_pct": round(pnl_pct, 2),
        "current_price": current_price,
        "entry_price": entry,
        "size": size,
        "direction": direction,
    }


def update_positions():
    """
    Poll for current prices and update all tracked positions.
    Logs to CSV. Returns list of updated position dicts.
    """
    prices = _fetch_current_prices()
    updated = []

    for cid, pos in list(_positions.items()):
        current_price = prices.get(cid)
        pnl = compute_pnl(pos, current_price)
        pos.update(pnl)

        logger.info(
            "Position: %s | %s | entry=%.4f | current=%.4f | PnL=$%.2f (%+.1f%%)",
            pos.get("market_title", "?")[:40],
            pos.get("direction", "?"),
            pos.get("entry_price", 0),
            pnl["current_price"],
            pnl["pnl_usd"],
            pnl["pnl_pct"],
        )

        _append_csv(pos, current_price=pnl["current_price"])
        updated.append(pos)

    return updated


def _append_csv(pos: dict, current_price: float | None):
    """Append a row to the CSV log file."""
    try:
        file_exists = os.path.isfile(config.CSV_LOG_FILE)
        with open(config.CSV_LOG_FILE, "a", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow([
                    "timestamp", "conditionId", "market_title", "direction",
                    "entry_price", "current_price", "size", "pnl_usd",
                    "pnl_pct", "status",
                ])
            writer.writerow([
                pos.get("timestamp", datetime.utcnow().isoformat()),
                pos.get("conditionId", ""),
                pos.get("market_title", ""),
                pos.get("direction", ""),
                pos.get("entry_price", ""),
                current_price if current_price is not None else "",
                pos.get("size", ""),
                pos.get("pnl_usd", ""),
                pos.get("pnl_pct", ""),
                pos.get("status", ""),
            ])
    except OSError as e:
        logger.warning("Failed to write CSV: %s", e)


def summary() -> dict:
    """
    Return a summary dict of all tracked positions and total P&L.
    """
    total_pnl = 0.0
    total_size = 0.0
    count = len(_positions)
    for pos in _positions.values():
        total_pnl += float(pos.get("pnl_usd", 0))
        total_size += float(pos.get("size", 0))

    return {
        "open_positions": count,
        "total_pnl_usd": round(total_pnl, 2),
        "total_size_usdc": round(total_size, 2),
        "positions": list(_positions.values()),
    }


def count_open_positions() -> int:
    """Return number of currently tracked open positions."""
    return len(_positions)


def get_open_positions() -> list[dict]:
    """Return list of open positions."""
    return list(_positions.values())