"""
config.py — All tunable thresholds and settings in one place.
Edit this file to adjust strategy parameters without touching business logic.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ─── Polymarket API ───────────────────────────────────────────────────────────
POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")
POLYMARKET_FUNDER_ADDRESS = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
POLYMARKET_API_KEY = os.getenv("POLYMARKET_API_KEY", "")
POLYMARKET_SECRET = os.getenv("POLYMARKET_SECRET", "")
POLYMARKET_PASSPHRASE = os.getenv("POLYMARKET_PASSPHRASE", "")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ─── API Endpoints ────────────────────────────────────────────────────────────
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"
CLOB_API_CHAIN_ID = 137  # Polygon mainnet

# ─── Scanner ──────────────────────────────────────────────────────────────────
SCAN_INTERVAL_MINUTES = 15
MIN_PROBABILITY = 0.88   # 88% — lowest acceptable implied probability
MAX_PROBABILITY = 0.96   # 96% — highest acceptable implied probability
MIN_STALE_HOURS = 6      # hours since last trade
MIN_LIQUIDITY_USDC = 500 # minimum orderbook depth
MAX_CANDIDATES_PER_SCAN = 50  # cap for Gamma API results

# ─── AI Validator ─────────────────────────────────────────────────────────────
CLAUDE_MODEL = "claude-sonnet-4-20250514"
MIN_CONFIDENCE_SCORE = 75  # 0-100, minimum to proceed with a bet
VALIDATOR_CACHE_TTL_MINUTES = 30
MAX_BATCH_SIZE = 5  # max candidates per validation batch (rate-limit safety)

# ─── Risk ─────────────────────────────────────────────────────────────────────
# DRY_RUN: when True, all order submissions are logged but NOT sent.
# Set to False only after you've verified everything in a test environment.
DRY_RUN = True

MAX_OPEN_POSITIONS = 5
PER_TRADE_PERCENT = 0.02  # 2% of available USDC balance per trade
HARD_EXIT_PRICE = 0.80    # exit a position if price drops below this
MIN_RESOLVE_HOURS = 24    # never bet on markets resolving in < 24h
ORDER_TTL_HOURS = 2       # cancel unfilled limit orders after 2h

# ─── Tracker ──────────────────────────────────────────────────────────────────
TRACK_POLL_INTERVAL_MINUTES = 5
CSV_LOG_FILE = "positions.csv"

# ─── Retry ────────────────────────────────────────────────────────────────────
MAX_RETRIES = 5
BASE_RETRY_DELAY_SECONDS = 1.0
RETRY_BACKOFF_FACTOR = 2.0
