"""
scanner.py — Polls Gamma API for stale high-probability markets.

Every SCAN_INTERVAL_MINUTES, queries Gamma for markets with current
YES/NO prices in the 0.88-0.96 range, filters to those last traded
6+ hours ago with adequate liquidity, and returns a ranked list.

Field mapping (Gamma API v3):
  question       -> market title
  outcomePrices  -> ["0.88", "0.12"] list of price strings
  outcomes       -> ["Yes", "No"] list of outcome names
  clobTokenIds   -> ["token_id_yes", "token_id_no"]
  endDate        -> ISO-8601 end date string
  updatedAt      -> ISO-8601 last update timestamp (staleness proxy)
  bestBid/Ask    -> current top of book
  lastTradePrice -> last traded price
  conditionId    -> unique market identifier
"""

import time
import logging
from datetime import datetime, timezone
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config

logger = logging.getLogger(__name__)


def _session() -> requests.Session:
    """Return a requests session with exponential-backoff retry."""
    s = requests.Session()
    retries = Retry(
        total=config.MAX_RETRIES,
        backoff_factor=config.BASE_RETRY_DELAY_SECONDS,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    return s


def fetch_markets(offset: int = 0, limit: int = 50, tag: str = "all") -> list[dict[str, Any]]:
    """
    Query Gamma API for open (active) markets sorted by volume.
    Use tag="all" for all categories, or a specific category tag.
    """
    url = f"{config.GAMMA_API_BASE}/markets"
    params = {
        "closed": "false",
        "limit": limit,
        "offset": offset,
        "order": "volume",
        "tag": tag,
    }
    try:
        resp = _session().get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        logger.debug("Gamma API returned %d markets (offset=%d, tag=%s)", len(data), offset, tag)
        return data
    except requests.RequestException as e:
        logger.error("Gamma API request failed: %s", e)
        return []


def fetch_orderbook(token_id: str) -> dict[str, Any] | None:
    """Fetch orderbook snapshot for a market's outcome token from CLOB.
    Uses /book endpoint which returns dicts: [{'price': '0.01', 'size': '100'}, ...]
    """
    url = f"{config.CLOB_API_BASE}/book"
    params = {"token_id": token_id}
    try:
        resp = _session().get(url, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        logger.warning("Orderbook fetch failed for token %s: %s", token_id, e)
        return None


def compute_liquidity(orderbook: dict[str, Any] | None) -> float:
    """Sum best-bid and best-ask liquidity in USDC from top 5 levels.
    Handles both array format [[price, size], ...] and dict format [{'price': ..., 'size': ...}, ...]
    """
    if not orderbook:
        return 0.0
    total = 0.0
    for side in ("bids", "asks"):
        orders = orderbook.get(side, [])
        if orders:
            for o in orders[:5]:
                try:
                    if isinstance(o, dict):
                        price = float(o.get("price", 0))
                        size = float(o.get("size", 0))
                    else:
                        price = float(o[0])
                        size = float(o[1])
                    total += price * size
                except (IndexError, ValueError, TypeError):
                    continue
    return total


def _normalize_time(raw: str | None) -> datetime | None:
    """Parse an ISO-8601 string to a naive UTC datetime. Handles Z and +00:00."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def market_stale_hours(market: dict[str, Any]) -> float | None:
    """
    Hours since this market was last updated.
    Uses updatedAt as a proxy for 'last trade' — a market that hasn't
    been updated in 6+ hours has had no price movement or trades.
    """
    raw = market.get("updatedAt")
    dt = _normalize_time(raw)
    if dt is None:
        return None
    delta = datetime.utcnow() - dt
    return delta.total_seconds() / 3600.0


def get_outcome_prices(market: dict[str, Any]) -> tuple[float, float]:
    """
    Return (YES_price, NO_price) from market.
    Gamma API returns outcomePrices as a list of price strings.
    """
    prices_raw = market.get("outcomePrices")
    if not prices_raw:
        return (0.0, 0.0)

    if isinstance(prices_raw, list) and len(prices_raw) >= 2:
        try:
            return (float(prices_raw[0]), float(prices_raw[1]))
        except (ValueError, TypeError):
            pass
    elif isinstance(prices_raw, str):
        import json
        try:
            prices = json.loads(prices_raw)
            if isinstance(prices, list) and len(prices) >= 2:
                return (float(prices[0]), float(prices[1]))
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    # Fallback: use bestBid/bestAsk
    try:
        bid = float(market.get("bestBid", 0))
        ask = float(market.get("bestAsk", 1))
        mid = (bid + ask) / 2.0
        return (round(mid, 4), round(1.0 - mid, 4))
    except (ValueError, TypeError):
        return (0.0, 0.0)


def get_token_ids(market: dict[str, Any]) -> tuple[str, str]:
    """
    Return (yes_token_id, no_token_id) from clobTokenIds.
    The field can be a list or a JSON-encoded string.
    """
    raw = market.get("clobTokenIds", [])
    ids = raw
    if isinstance(raw, str):
        import json
        try:
            ids = json.loads(raw)
        except (json.JSONDecodeError, ValueError, TypeError):
            return ("", "")
    if isinstance(ids, list) and len(ids) >= 2:
        return (str(ids[0]), str(ids[1]))
    return ("", "")


def get_market_title(market: dict[str, Any]) -> str:
    """Get human-readable market title."""
    return market.get("question") or market.get("title") or market.get("slug", "?")


def is_high_probability(yes_price: float, no_price: float) -> tuple[bool, str]:
    """
    Check whether *either* YES or NO is in the 0.88-0.96 range.
    Returns (is_high_prob, direction) where direction is 'YES' or 'NO'.
    """
    if config.MIN_PROBABILITY <= yes_price <= config.MAX_PROBABILITY:
        return True, "YES"
    if config.MIN_PROBABILITY <= no_price <= config.MAX_PROBABILITY:
        return True, "NO"
    return False, ""


def rank_candidates(candidates: list[dict]) -> list[dict]:
    """
    Rank candidates by a composite score: staleness × liquidity.
    Higher score = more attractive. Returns list sorted descending.
    """
    scored = []
    for m in candidates:
        age = m.get("_staleness_hours", 0)
        if age <= 0:
            continue

        # Use primary token's orderbook
        yes_token, no_token = get_token_ids(m)
        primary_token = yes_token if m.get("_direction") == "YES" else no_token
        ob = fetch_orderbook(primary_token) if primary_token else None
        liq = compute_liquidity(ob)

        # Staleness score: more hours since last trade = higher
        staleness = min(age / 24.0, 3.0)  # cap at 3 days equivalent
        # Liquidity score: $500 min, scale up but cap at $10k
        liq_score = min(liq / 1000.0, 10.0) if liq >= config.MIN_LIQUIDITY_USDC else 0.0
        composite = staleness * liq_score

        m["_score"] = round(composite, 2)
        m["_liquidity_usdc"] = round(liq, 2)
        m["_orderbook"] = ob
        m["_yes_token"] = yes_token
        m["_no_token"] = no_token
        scored.append(m)

    scored.sort(key=lambda x: x["_score"], reverse=True)
    return scored


def parse_end_date(market: dict) -> str:
    """Get the end date of a market as a string."""
    return market.get("endDate") or market.get("endDateIso") or ""


def scan() -> list[dict]:
    """
    Main scan entry point.
    Returns a ranked list of candidate market dicts ready for AI validation.
    Each candidate has extra keys: _score, _staleness_hours, _liquidity_usdc,
    _direction, _orderbook, _yes_token, _no_token.
    """
    logger.info("=== Starting market scan ===")
    all_markets = []
    offset = 0
    limit = config.MAX_CANDIDATES_PER_SCAN

    while offset < 200:
        batch = fetch_markets(offset=offset, limit=limit)
        if not batch:
            break
        all_markets.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
        time.sleep(0.3)

    logger.info("Total markets fetched: %d", len(all_markets))

    candidates = []
    for m in all_markets:
        title = get_market_title(m)
        if not title or title == "?":
            continue

        # Skip UST (Ultra Short Term) markets — they have no CLOB orderbooks
        yes_token, no_token = get_token_ids(m)
        if not yes_token or not no_token:
            continue

        yes_p, no_p = get_outcome_prices(m)
        if yes_p == 0.0 and no_p == 0.0:
            continue  # no price data available

        is_hp, direction = is_high_probability(yes_p, no_p)
        if not is_hp:
            continue

        # Staleness check
        age = market_stale_hours(m)
        if age is None or age < config.MIN_STALE_HOURS:
            continue

        # Quick liquidity check using direct Gamma fields
        best_bid = float(m.get("bestBid", 0))
        best_ask = float(m.get("bestAsk", 0))
        approx_liq = (best_bid + best_ask) * 1000  # rough estimate
        if approx_liq < config.MIN_LIQUIDITY_USDC:
            continue

        # Check minimum resolve time
        end_date_str = parse_end_date(m)
        if end_date_str:
            end_dt = _normalize_time(end_date_str)
            if end_dt:
                hours_to_resolve = (end_dt - datetime.utcnow()).total_seconds() / 3600.0
                if hours_to_resolve < config.MIN_RESOLVE_HOURS:
                    logger.debug("Skipping '%s' — resolves in <24h (%.1f h)", title, hours_to_resolve)
                    continue

        m["_yes_price"] = yes_p
        m["_no_price"] = no_p
        m["_direction"] = direction
        m["_staleness_hours"] = round(age, 1)
        m["_title"] = title
        candidates.append(m)

    logger.info("High-prob stale candidates before ranking: %d", len(candidates))

    ranked = rank_candidates(candidates)
    ranked = ranked[:config.MAX_CANDIDATES_PER_SCAN]

    logger.info("Ranked candidates: %d", len(ranked))
    for c in ranked[:10]:
        logger.info(
            "  #%-3s score=%.2f | %s | dir=%-3s stale=%.1fh liq=$%.0f",
            c.get("conditionId", "")[:8],
            c["_score"],
            c["_title"][:50],
            c["_direction"],
            c["_staleness_hours"],
            c.get("_liquidity_usdc", 0),
        )

    return ranked