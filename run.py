#!/usr/bin/env python3
"""
run.py — MVP entry point for Stale High-Probability Hunter.

One command to scan, validate, and report:
    python run.py              # dry-run scan + AI validation
    python run.py --validate   # scan + AI validation, full reasoning output
    python run.py --once       # single full cycle (still DRY_RUN)

Set DRY_RUN=False in config.py or use config.DRY_RUN = False to enable real orders.
"""

import logging
import sys
import os

# Ensure we're in the project root
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Setup minimal logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)

import config
from scanner import scan
from ai_validator import validate_batch
from risk import validate_trade

def main():
    args = set(sys.argv[1:])
    validate_only = "--validate" in args or "-v" in args

    print("=" * 60)
    print("  STALE HIGH-PROBABILITY HUNTER  (MVP)")
    print(f"  DRY_RUN = {config.DRY_RUN}")
    if config.DRY_RUN:
        print("  ⚠️  No real orders will be placed")
    print("=" * 60)

    # Step 1: Scan
    print("\n[1/3] Scanning Gamma API for stale high-probability markets...")
    candidates = scan()
    print(f"  → {len(candidates)} candidates found")

    if not candidates:
        print("\n  No markets in the 88-96% range right now.")
        print("  Markets at these prices are rare — the scanner is working correctly.")
        print("  The strategy waits for opportunities; none exist currently.")
        return

    # Step 2: Validate
    print(f"\n[2/3] AI-validating {len(candidates)} candidates...")
    approved = validate_batch(candidates)
    print(f"  → {len(approved)} markets approved by AI")

    if not approved:
        print("\n  No markets passed AI validation.")
        print("  (Claude found reasons to skip each candidate)")
        return

    # Step 3: Report
    print(f"\n[3/3] Results")
    for market, result in approved:
        title = market.get("_title", "?")[:60]
        direction = result.get("direction", "?")
        confidence = result.get("confidence", 0)
        reasoning = result.get("reasoning", "")[:120]
        print(f"\n  ✅ {title}")
        print(f"     Direction: {direction}  |  Confidence: {confidence}/100")
        print(f"     Reasoning: {reasoning}")
        risk_flags = result.get("risk_flags", [])
        if risk_flags:
            print(f"     Risk flags: {', '.join(risk_flags)}")

    if validate_only:
        print("\n  ✓ Validate mode — stopping here")
        return

    print("\n  ✓ MVP scan complete.")
    print("  (Orders skipped — set DRY_RUN=False to enable real trading)")

if __name__ == "__main__":
    main()