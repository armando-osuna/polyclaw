"""
demo.py — Showcase the Stale High-Probability Hunter agent's internals.
Runs against live Gamma API and shows exactly how it works.
"""

import logging, sys
logging.basicConfig(level=logging.WARNING, stream=sys.stdout, format="%(message)s")

import requests
import config
from scanner import get_outcome_prices, get_market_title, get_token_ids, market_stale_hours, is_high_probability

print("=" * 65)
print("  STALE HIGH-PROBABILITY HUNTER  —  LIVE DEMO")
print("  Connecting to Polymarket Gamma API...")
print("=" * 65)

# Fetch real markets from multiple categories
all_markets = []
for tag in ['politics', 'crypto', 'sports']:
    r = requests.get(f"{config.GAMMA_API_BASE}/markets",
                     params={'limit': 15, 'closed': 'false', 'tag': tag}, timeout=10)
    if r.ok:
        all_markets.extend(r.json())

print(f"\n📡 Scanning {len(all_markets)} live markets...")
print(f"   Target range: ${config.MIN_PROBABILITY}–${config.MAX_PROBABILITY}  (88-96¢)")
print(f"   Minimum staleness: {config.MIN_STALE_HOURS}+ hours since last trade")
print()

# Show active markets with real prices (closest to target range)
active = []
for m in all_markets:
    y, n = get_outcome_prices(m)
    yt, nt = get_token_ids(m)
    title = get_market_title(m)
    if y > 0.01 and yt:
        age = market_stale_hours(m)
        is_hp, direction = is_high_probability(y, n)
        active.append((y, title, n, age, is_hp, direction, m.get('bestBid', '?'), m.get('bestAsk', '?')))

active.sort(reverse=True)

print("─── Live Markets on Polymarket (sorted by YES price) ───")
print(f"     {'Price':>7}  {'Direction':>9}  Stale  Market")
print(f"     {'──────':>7}  {'─────────':>9}  ─────  ─────")
for y, t, n, age, is_hp, direction, bid, ask in active[:12]:
    flag = "✅ TARGET" if is_hp else ("─ near-miss ─" if y > 0.75 else "")
    print(f"  {flag:>13}  YES=${y:<5.3f}  NO=${n:<5.3f}  {age:>4.1f}h  {t[:45]}")

print()
print("─── How the Agent Works ───")
print("""
  1. SCAN ──── Gamma API → filter 88-96% price → stale ≥6h → rank by score
  2. AI CHECK ─ Claude web search + sentiment scoring (needs ANTHROPIC_API_KEY)
  3. RISK ──── Position limits, trade size caps, hard exit at $0.80
  4. EXECUTE ─ Limit order at mid price (DRY_RUN mode, no real orders)
  5. TRACK ─── P&L logging to positions.csv

  🔒 DRY_RUN = True — no real money moves without manual confirmation
""")

print("─── Configuration ───")
for k, v in [
    ("DRY_RUN", config.DRY_RUN),
    ("Min probability", config.MIN_PROBABILITY),
    ("Max probability", config.MAX_PROBABILITY),
    ("Min staleness", f"{config.MIN_STALE_HOURS}h"),
    ("Min liquidity", f"${config.MIN_LIQUIDITY_USDC}"),
    ("Max positions", config.MAX_OPEN_POSITIONS),
    ("Per-trade size", f"{config.PER_TRADE_PERCENT*100}%"),
    ("Hard exit", f"${config.HARD_EXIT_PRICE}"),
]:
    print(f"  {k:>18} = {v}")
print()

print("─── Commands ───")
print("  python run.py           # Quick scan")
print("  python run.py --validate # + AI analysis (needs .env API key)")
print("  python main.py --once   # Full cycle (DRY_RUN)")
print("  python main.py          # Continuous loop every 15 min")
print()

print(f"📊 Market snapshot: {len(active)} active CLOB markets found")
near_miss = sum(1 for y, *_ in active if 0.75 < y < 0.88)
print(f"   {near_miss} markets in the 75-88% range (just below threshold)")
print(f"   Scanner is tuned to catch 88-96% when they appear")
print()