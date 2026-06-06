# Polymarket Stale High-Probability Hunter

An automated trading agent that hunts stale high-probability markets (88–96% range)
on Polymarket that haven't repriced in 6+ hours, with an AI validation layer that
checks each opportunity before betting.

**Thesis:** These markets are often just forgotten. The math is forgiving (you're
buying near-certainties), competition is low, and mispricing persists because no
one is watching.

## Architecture

```
main.py  (orchestration loop — 15-min cycle)
  ├── scanner.py      — polls Gamma API, filters to stale high-prob candidates
  ├── ai_validator.py — two-step AI pipeline (web search → Claude scoring)
  ├── risk.py         — hard risk rules (position limits, stop-loss, DRY_RUN)
  ├── executor.py     — CLOB order placement, cancellation, WebSocket fills
  ├── tracker.py      — position tracking, P&L calculation, CSV logging
  └── config.py       — all tunable thresholds in one file
```

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Set up credentials

```bash
cp .env.example .env
```

Edit `.env` and add:
- `POLYMARKET_PRIVATE_KEY` — your Polygon wallet private key (with 0x prefix)
- `POLYMARKET_FUNDER_ADDRESS` — your wallet address
- `ANTHROPIC_API_KEY` — your Anthropic API key for Claude

### 3. Run in dry-run mode (safe — no real orders)

```bash
python main.py
```

This runs the full loop: scan → AI validate → risk check → **log only** (no orders
submitted). All decisions and reasoning are logged to `trader.log` and `ai_reasoning.log`.

### 4. Validate mode (scan + AI only, no execution)

```bash
python main.py --validate --once
```

Shows what the AI would approve without attempting any orders. Great for tuning
thresholds.

### 5. Single pass

```bash
python main.py --once
```

### 6. ⚠️  Go live

```bash
python main.py --live
```

You'll be asked to type `yes` to confirm. This disables `DRY_RUN` and submits
real limit orders to Polymarket.

## Tuning the Strategy

All thresholds are in `config.py`. Key knobs:

| Parameter | Default | What it does |
|---|---|---|
| `MIN_PROBABILITY` | 0.88 | Lowest implied probability to consider |
| `MAX_PROBABILITY` | 0.96 | Highest implied probability to consider |
| `MIN_STALE_HOURS` | 6 | Minimum hours since last trade |
| `MIN_LIQUIDITY_USDC` | 500 | Minimum orderbook depth |
| `MIN_CONFIDENCE_SCORE` | 75 | Minimum Claude confidence to place a bet |
| `MAX_OPEN_POSITIONS` | 5 | Simultaneous position limit |
| `PER_TRADE_PERCENT` | 0.02 | % of USDC balance per trade (2%) |
| `HARD_EXIT_PRICE` | 0.80 | Exit position if price drops below this |
| `DRY_RUN` | `True` | **Master safety switch** — set to `False` to submit real orders |

## Safety

- **DRY_RUN is wired through every order submission path.** It defaults to `True`.
  You must deliberately disable it (either via `--live` flag or editing `config.py`).
- Position limits: max 5 simultaneous open positions.
- Per-trade size capped at 2% of available USDC balance.
- Hard exit: auto-cancel if price drops below $0.80.
- Never bets on markets resolving in less than 24 hours.
- All AI reasoning logged to `ai_reasoning.log` for manual review.
- Exponential backoff retry on all API calls.

## Logs

| File | Contents |
|---|---|
| `trader.log` | Full runtime log — every scan, skip, bet, and error |
| `ai_reasoning.log` | Every Claude response: reasoning, confidence, risk flags |
| `positions.csv` | Time-series of all positions and P&L |

## Requirements

- Python 3.10+
- Polygon wallet with USDC on Polymarket
- Anthropic API key with access to `claude-sonnet-4-20250514`

## License

MIT — use at your own risk. This is trading software; test thoroughly before
using with real funds.