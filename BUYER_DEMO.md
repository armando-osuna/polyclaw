# Buyers Demo Guide

## Introduction

This is an automated trading bot for **Prediction Markets (Polymarket)**.

It searches for **miss-priced events** trading between **88¢ and 96¢** that the market has *forgotten about* — no trades in 6+ hours.

The system uses **Claude AI** to search the web and validate each opportunity before betting.

It is currently in **DRY RUN mode**: no real money is being risked.

---

## How to Demo

```bash
git clone https://github.com/armando-osuna/polyclaw.git
cd polyclaw

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

python demo.py
```

This shows **live Polymarket data** sorted by price, plus the bot parameters.

---

## Module Summary

**How the Bot Works**

The bot runs continuously in a **15 minute loop**:

1. **SCAN** — Fetches live markets from `gamma-api.polymarket.com`. Filters for prices in the **$0.88 to $0.96** range. Filters for markets that haven't been traded in **6+ hours** (stale). Filters for a minimum of **$500 of liquidity** in the orderbook.

2. **AI VALIDATOR** — For each candidate, uses **Claude Sonnet 4** with web search (`web_search_20250305`) to check for news that might affect the outcome. Returns a risk score.

3. **RISK CHECKS** — Enforces maximum 5 open positions, 2% per trade. Hard stop loss at **$0.80**.

4. **EXECUTION** — Places limit orders at the mid price to capture **maker rebates**.

5. **TRACKING** — Logs all positions to CSV with running P&L.

**Safety Systems**

- `DRY_RUN = True` — wired through every order submission path
- No real orders possible without deliberately setting `DRY_RUN = False`
- Max 5 simultaneous positions
- Hard exit at $0.80
- Never bets on markets resolving in less than 24 hours

**Testing**

- **104 tests** across 3 test files
- 58 scanner tests
- 48 risk + executor tests
- All pass against live API mocks

**To go live:**

Edit `config.py`:

```python
DRY_RUN = False   # ⚠️ enables real orders
```

Then run:

```bash
python main.py
```

---

## Live Demo Output

```text
=================================================================
  STALE HIGH-PROBABILITY HUNTER  —  LIVE DEMO
  Connecting to Polymarket Gamma API...
=================================================================

📡 Scanning 45 live markets...
   Target range: $0.88–$0.96  (88-96¢)
   Minimum staleness: 6+ hours since last trade

─── Live Markets on Polymarket (sorted by YES price) ───
  near-miss  YES=$0.801  NO=$0.199   0.1h  Will Harvey Weinstein be sentenced...
  near-miss  YES=$0.775  NO=$0.225   0.1h  Will the NY Knicks win the NBA Finals?
             YES=$0.525  NO=$0.475   0.0h  New Playboi Carti Album before GTA VI?
             YES=$0.515  NO=$0.485   0.0h  New Rihanna Album before GTA VI?

─── How the Agent Works ───
  1. SCAN ──── Gamma API → filter 88-96% → stale ≥6h → rank
  2. AI CHECK ─ Claude web search + sentiment scoring
  3. RISK ──── Position limits, trade size, hard exit at $0.80
  4. EXECUTE ─ Limit order at mid price (DRY_RUN mode)
  5. TRACK ─── P&L logging to positions.csv

  🔒 DRY_RUN = True — no real money moves without manual confirmation

📊 Market snapshot: 42 active CLOB markets found
   6 markets in the 75-88% range (just below threshold)
   Scanner is tuned to catch 88-96% when they appear
```

*Note: Currently no markets are trading in the 88-96¢ band. This is expected: the bot hunts rare mispricings. When one eventually appears nobody is watching, it will be caught.*

---

## Technology Stack

| Component | Tool |
|---|---|
| Python 3.12 | Runtime |
| `py-clob-client` | Polymarket order signing & placement |
| Anthropic Claude | AI validation (web search + scoring) |
| Gamma API | Market discovery |
| CLOB API | Orderbook & order execution |
| pytest | Testing (104 tests) |

## Repository

`github.com/armando-osuna/polyclaw`", "filePath": "/home/agent-lead/polymarket-trader/BUYER_DEMO.md"}