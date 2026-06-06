"""
ai_validator.py — Two-step AI validation pipeline.

Step 1: Web search scan for disqualifying news.
Step 2: Claude sentiment scoring — returns confidence, direction, reasoning.

Caches results for VALIDATOR_CACHE_TTL_MINUTES to avoid redundant API calls.
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import anthropic

import config

logger = logging.getLogger(__name__)

# ─── Cache ────────────────────────────────────────────────────────────────────
_cache: dict[str, dict[str, Any]] = {}  # market_id -> {result, expires_at}


def _cache_key(market: dict) -> str:
    return market.get("conditionId") or market.get("id", "")


def _cache_get(key: str) -> dict | None:
    entry = _cache.get(key)
    if entry and entry["expires_at"] > time.time():
        return entry["result"]
    if key in _cache:
        del _cache[key]
    return None


def _cache_set(key: str, result: dict):
    _cache[key] = {
        "result": result,
        "expires_at": time.time() + config.VALIDATOR_CACHE_TTL_MINUTES * 60,
    }


# ─── Web Search Step ──────────────────────────────────────────────────────────

def _build_search_query(market: dict) -> str:
    """Build a concise search query from market title and resolution criteria."""
    title = market.get("_title", "") or market.get("question", "")
    desc = market.get("description", "") or market.get("outcomeDescription", "")
    criteria = market.get("resolutionSource") or market.get("resolution", "")
    return f"title={title} | criteria={criteria}"


def web_search_scan(market: dict) -> dict:
    """
    Step 1: Use Claude with web_search_20250305 to check for disqualifying news.

    Returns dict with:
      - "disqualified": bool
      - "findings": str (summary of what was found)
      - "sources": list of URLs
    """
    search_query = _build_search_query(market)

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    system_prompt = (
        "You are a research assistant checking whether recent news affects the outcome "
        "of a prediction market. Use the web_search_20250305 tool to search for the "
        "latest updates. Be thorough — look for any event, announcement, or development "
        "that would change the probability of the market resolving to YES or NO."
    )

    user_msg = (
        f"I need to check if there's any breaking news or recent developments "
        f"that would affect this prediction market. Search for recent updates and "
        f"tell me if the outcome has effectively been decided.\n\n"
        f"Market: {search_query}\n\n"
        f"Search for: '{market.get('_title', market.get('question', ''))} latest news' and "
        f"'{market.get('_title', market.get('question', ''))} result update'\n\n"
        f"If you find any news that definitively indicates the outcome "
        f"(e.g., an event already happened, a decision was made, a match was played), "
        f"report it. If nothing meaningful was found, say 'no disqualifying news found.'"
    )

    try:
        resp = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )
    except Exception as e:
        logger.error("Web search step failed for market '%s': %s",
                      market.get("_title", "?"), e)
        return {"disqualified": False, "findings": f"Search failed: {e}", "sources": []}

    text = ""
    for block in resp.content:
        if hasattr(block, "text"):
            text += block.text

    disqualified = any(phrase in text.lower()
                       for phrase in ["outcome is decided", "result is known",
                                      "event has concluded", "already happened",
                                      "already determined", "definitive result"])

    logger.info("Web search for '%s': disqualified=%s | %s",
                market.get("_title", "?")[:50], disqualified,
                text[:150].replace("\n", " "))

    return {
        "disqualified": disqualified,
        "findings": text.strip(),
        "sources": [],  # we don't parse URLs from Claude response reliably here
    }


# ─── Claude Scoring Step ──────────────────────────────────────────────────────

def claude_score(market: dict, web_results: dict) -> dict:
    """
    Step 2: Send market info + web results to Claude for a structured score.

    Returns dict with:
      - confidence: int 0-100
      - direction: "YES" | "NO" | "SKIP"
      - reasoning: str
      - risk_flags: list[str]
    """
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    direction = market.get("_direction", "YES")
    price = market.get("_yes_price", 0) if direction == "YES" else market.get("_no_price", 0)
    stale_hours = market.get("_staleness_hours", 0)
    title = market.get("_title", "?") or market.get("question", "?")
    outcomes = market.get("outcomes", [])
    end_date = market.get("endDate", "N/A")
    liquidity = market.get("_liquidity_usdc", 0)
    description = market.get("description", "") or market.get("outcomeDescription", "")

    user_msg = (
        f"Analyze this Polymarket prediction market for a trade opportunity:\n\n"
        f"Title: {title}\n"
        f"Description: {description}\n"
        f"Current {direction} price: ${price:.4f} (implied {price*100:.1f}% probability)\n"
        f"Time since last trade: {stale_hours} hours\n"
        f"Orderbook liquidity: ${liquidity:.0f} USDC\n"
        f"Resolution date: {end_date}\n\n"
        f"Web search findings:\n{web_results.get('findings', 'None')}\n\n"
        f"Return a JSON object with these keys:\n"
        f'  - "confidence": integer 0-100 (how confident you are in the prediction)\n'
        f'  - "direction": "YES", "NO", or "SKIP"\n'
        f'  - "reasoning": string explaining your analysis\n'
        f'  - "risk_flags": array of strings describing any risks\n\n'
        f"Rules:\n"
        f"- Only choose YES or NO if you are highly confident (>75% confidence)\n"
        f"- Choose SKIP if the outcome is uncertain, news is contradictory, or data is insufficient\n"
        f"- If web search found disqualifying news (outcome already decided), choose SKIP\n"
        f"- The current price means the market is pricing this at {price*100:.1f}% — we want to bet "
        f"only when the market is underpricing a near-certain outcome"
    )

    system_prompt = (
        "You are a prediction-market analyst. You evaluate markets carefully and "
        "return only valid JSON. You are conservative — you prefer SKIP over a "
        "risky bet. You factor in recency of information, market depth, and "
        "resolution criteria."
    )

    try:
        resp = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )
    except Exception as e:
        logger.error("Claude scoring failed for '%s': %s", title, e)
        return {
            "confidence": 0,
            "direction": "SKIP",
            "reasoning": f"API call failed: {e}",
            "risk_flags": ["api_error"],
        }

    text = ""
    for block in resp.content:
        if hasattr(block, "text"):
            text += block.text

    # Extract JSON from the response (Claude may wrap in markdown fences)
    json_str = text.strip()
    if "```json" in json_str:
        json_str = json_str.split("```json")[1].split("```")[0].strip()
    elif "```" in json_str:
        json_str = json_str.split("```")[1].split("```")[0].strip()

    try:
        result = json.loads(json_str)
    except json.JSONDecodeError:
        logger.warning("Failed to parse Claude JSON. Raw: %s", text[:300])
        return {
            "confidence": 0,
            "direction": "SKIP",
            "reasoning": f"Parse error. Raw: {text[:200]}",
            "risk_flags": ["parse_error"],
        }

    # Validate structure
    for key in ("confidence", "direction", "reasoning"):
        if key not in result:
            logger.warning("Claude response missing key '%s'", key)
            result[key] = 0 if key == "confidence" else ("SKIP" if key == "direction" else "")

    result.setdefault("risk_flags", [])

    logger.info(
        "Claude score for '%s': confidence=%d, direction=%s, flags=%s",
        title[:50], result.get("confidence", 0), result.get("direction", "?"),
        result.get("risk_flags", []),
    )

    return result


# ─── Public API ───────────────────────────────────────────────────────────────

def validate_market(market: dict) -> dict | None:
    """
    Full validation pipeline for a single candidate market.

    Returns a validated result dict with confidence/direction if approved,
    or None if disqualified at any step. All results are cached.

    Side effect: logs every decision with reasoning.
    """
    ck = _cache_key(market)
    cached = _cache_get(ck)
    if cached:
        logger.info("Cache hit for market %s", ck[:12])
        return cached if cached.get("approved") else None

    market_title = market.get("_title", "?")[:60]

    # Step 1: Web search
    logger.info("Step 1 — Web search for '%s'", market_title)
    web_results = web_search_scan(market)

    if web_results["disqualified"]:
        logger.info("DISQUALIFIED by web search: %s", market_title)
        result = {
            "approved": False,
            "confidence": 0,
            "direction": "SKIP",
            "reasoning": f"Web search found disqualifying news: {web_results['findings'][:200]}",
            "risk_flags": ["disqualifying_news"],
            "web_results": web_results,
        }
        _cache_set(ck, result)
        return None

    # Step 2: Claude scoring
    logger.info("Step 2 — Claude scoring for '%s'", market_title)
    score = claude_score(market, web_results)

    if score["direction"] == "SKIP" or score["confidence"] < config.MIN_CONFIDENCE_SCORE:
        logger.info(
            "SKIPPED by Claude: confidence=%d < %d or direction=SKIP for '%s'",
            score["confidence"], config.MIN_CONFIDENCE_SCORE, market_title,
        )
        result = {
            "approved": False,
            "confidence": score["confidence"],
            "direction": score["direction"],
            "reasoning": score["reasoning"],
            "risk_flags": score["risk_flags"],
            "web_results": web_results,
        }
        _cache_set(ck, result)
        return None

    # Approved
    result = {
        "approved": True,
        "confidence": score["confidence"],
        "direction": score["direction"],
        "reasoning": score["reasoning"],
        "risk_flags": score["risk_flags"],
        "web_results": web_results,
    }
    _cache_set(ck, result)

    logger.info(
        "APPROVED by AI: '%s' | confidence=%d | direction=%s",
        market_title, score["confidence"], score["direction"],
    )

    # Log full reasoning to file for review
    _log_reasoning(market, result)

    return result


def _log_reasoning(market: dict, result: dict):
    """Log all Claude reasoning to a local file for manual review."""
    try:
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        title = market.get("_title", "?")
        cid = market.get("conditionId", "?")
        entry = (
            f"=== {ts} ===\n"
            f"Market: {title}\n"
            f"ConditionID: {cid}\n"
            f"Direction: {result.get('direction', '?')}\n"
            f"Confidence: {result.get('confidence', 0)}\n"
            f"Reasoning: {result.get('reasoning', 'N/A')}\n"
            f"Risk Flags: {result.get('risk_flags', [])}\n"
            f"Web Findings: {result.get('web_results', {}).get('findings', 'N/A')[:300]}\n"
            f"{'─' * 60}\n"
        )
        with open("ai_reasoning.log", "a") as f:
            f.write(entry)
    except OSError as e:
        logger.warning("Failed to write reasoning log: %s", e)


def validate_batch(markets: list[dict]) -> list[tuple[dict, dict]]:
    """
    Validate a batch of candidates, respecting rate limits.
    Returns list of (market, result) tuples for approved markets.
    """
    approved: list[tuple[dict, dict]] = []
    for i, m in enumerate(markets):
        if i > 0 and i % config.MAX_BATCH_SIZE == 0:
            logger.info("Rate-limit pause after %d validations", i)
            time.sleep(2.0)
        result = validate_market(m)
        if result and result["approved"]:
            approved.append((m, result))
    return approved