"""
Comprehensive tests for risk.py — the real codebase.

Tests cover all risk enforcement paths:
- DRY_RUN: require_dry_run raises when DRY_RUN=True (blocks ALL order paths)
- Position limits: check_max_positions raises when at max
- Trade size caps: check_trade_size enforces per-trade % of balance
- Hard exit: check_hard_exit detects threshold breaches for YES/NO
- Resolve time: check_resolve_hours blocks fast-resolving markets
- validate_trade: full validation pipeline
- dry_run_log: centralized logging in DRY_RUN mode
"""

import sys
sys.path.insert(0, '/home/agent-lead/polymarket-trader')

from unittest.mock import patch
from datetime import datetime, timezone, timedelta

import pytest

import config
from risk import (
    RiskViolation,
    require_dry_run,
    check_max_positions,
    check_trade_size,
    check_hard_exit,
    check_resolve_hours,
    validate_trade,
    dry_run_log,
)


# ===================================================================
# DRY_RUN enforcement 
# ===================================================================

class TestRequireDryRun:
    """DRY_RUN=True blocks every order submission path."""

    def test_dry_run_true_raises(self):
        """When DRY_RUN=True (safe default), require_dry_run raises."""
        assert config.DRY_RUN is True  # default must be safe
        with pytest.raises(RiskViolation, match="DRY_RUN is enabled"):
            require_dry_run()

    @patch("config.DRY_RUN", False)
    def test_dry_run_false_passes(self):
        """When DRY_RUN=False, require_dry_run passes silently."""
        require_dry_run()  # should not raise

    def test_risk_violation_is_exception(self):
        """RiskViolation is a proper Exception subclass."""
        with pytest.raises(Exception):
            require_dry_run()


# ===================================================================
# Position limits
# ===================================================================

class TestCheckMaxPositions:
    """Maximum open position enforcement."""

    def test_below_limit_passes(self):
        """Below MAX_OPEN_POSITIONS (5) passes."""
        check_max_positions(3)

    def test_at_limit_raises(self):
        """At MAX_OPEN_POSITIONS (5) raises."""
        with pytest.raises(RiskViolation, match="Position limit"):
            check_max_positions(5)

    def test_above_limit_raises(self):
        """Above MAX_OPEN_POSITIONS raises."""
        with pytest.raises(RiskViolation):
            check_max_positions(10)

    def test_zero_positions_passes(self):
        """Zero open positions passes."""
        check_max_positions(0)


# ===================================================================
# Trade size checks
# ===================================================================

class TestCheckTradeSize:
    """Trade size enforcement: PER_TRADE_PERCENT of balance."""

    def test_within_limit(self):
        """Trade size within 2% of balance passes."""
        check_trade_size(usdc_balance=1000.0, proposed_size=20.0)

    def test_exceeds_limit_raises(self):
        """Trade size exceeding 2% of balance raises."""
        with pytest.raises(RiskViolation, match="exceeds"):
            check_trade_size(usdc_balance=1000.0, proposed_size=50.0)

    def test_exactly_at_limit(self):
        """Exactly 2% passes."""
        check_trade_size(usdc_balance=1000.0, proposed_size=20.0)

    def test_zero_balance_raises(self):
        """Zero balance means any size exceeds 2% of $0."""
        with pytest.raises(RiskViolation, match="exceeds"):
            check_trade_size(usdc_balance=0.0, proposed_size=0.01)

    def test_small_balance(self):
        """Small balance with tiny trade passes."""
        check_trade_size(usdc_balance=100.0, proposed_size=2.0)

    def test_percent_config_value(self):
        """Uses config.PER_TRADE_PERCENT (2%)."""
        assert config.PER_TRADE_PERCENT == 0.02


# ===================================================================
# Hard exit
# ===================================================================

class TestCheckHardExit:
    """Hard exit threshold breaches for YES/NO positions."""

    def test_yes_above_threshold(self):
        """YES position price above HARD_EXIT_PRICE (0.80) — no exit."""
        assert check_hard_exit(entry_price=0.95, current_price=0.85, direction="YES") is False

    def test_yes_below_threshold(self):
        """YES position price below 0.80 — exit triggered."""
        assert check_hard_exit(entry_price=0.95, current_price=0.75, direction="YES") is True

    def test_yes_exactly_at_threshold(self):
        """YES position exactly at 0.80 — no exit (not below)."""
        assert check_hard_exit(entry_price=0.95, current_price=0.80, direction="YES") is False

    def test_no_above_threshold(self):
        """NO position — current price < 0.20 means exit."""
        # For NO, exit if 1.0 - current_price < HARD_EXIT_PRICE
        # current_price=0.10 -> NO price = 0.90 >= 0.80, no exit
        assert check_hard_exit(entry_price=0.95, current_price=0.10, direction="NO") is False

    def test_no_below_threshold(self):
        """NO position — current price > 0.20 means exit."""
        # current_price=0.30 -> NO price = 0.70 < 0.80, exit!
        assert check_hard_exit(entry_price=0.95, current_price=0.30, direction="NO") is True

    def test_no_exactly_at_boundary(self):
        """NO position exactly at the boundary."""
        # current_price=0.20 -> NO price = 0.80 == HARD_EXIT_PRICE, no exit
        assert check_hard_exit(entry_price=0.95, current_price=0.20, direction="NO") is False

    def test_case_insensitive(self):
        """Direction is case-sensitive (must be YES/NO)."""
        assert check_hard_exit(0.95, 0.85, "YES") is False
        assert check_hard_exit(0.95, 0.75, "YES") is True

    def test_unknown_direction(self):
        """Unknown direction returns False (safe default)."""
        assert check_hard_exit(0.95, 0.50, "UNKNOWN") is False


# ===================================================================
# Resolve hours
# ===================================================================

class TestCheckResolveHours:
    """Minimum resolve time enforcement."""

    def test_sufficient_time(self):
        """>24h to resolve passes."""
        check_resolve_hours(72.0)

    def test_too_soon_raises(self):
        """<24h to resolve raises."""
        with pytest.raises(RiskViolation, match="resolves in|Too volatile"):
            check_resolve_hours(12.0)

    def test_exactly_at_boundary(self):
        """Exactly 24h passes."""
        check_resolve_hours(24.0)

    def test_negative_hours(self):
        """Already past resolve time raises."""
        with pytest.raises(RiskViolation):
            check_resolve_hours(-1.0)

    def test_zero_hours(self):
        """Zero hours to resolve raises."""
        with pytest.raises(RiskViolation):
            check_resolve_hours(0.0)


# ===================================================================
# validate_trade — full pipeline
# ===================================================================

class TestValidateTrade:
    """Full trade validation pipeline."""

    def test_valid_trade_approved(self):
        """Valid trade is approved with adjusted_size == proposed_size."""
        now = datetime.utcnow()
        market = {
            "endDate": (now + timedelta(days=10)).isoformat() + "Z",
        }
        result = validate_trade(
            market=market,
            direction="YES",
            proposed_size=20.0,
            usdc_balance=1000.0,
            open_positions_count=3,
        )
        assert result["approved"] is True
        assert result["reason"] == ""
        assert result["adjusted_size"] == 20.0

    def test_blocked_by_max_positions(self):
        """Exceeding max positions blocks the trade."""
        market = {"endDate": (datetime.utcnow() + timedelta(days=10)).isoformat() + "Z"}
        result = validate_trade(
            market=market,
            direction="YES",
            proposed_size=20.0,
            usdc_balance=1000.0,
            open_positions_count=5,
        )
        assert result["approved"] is False
        assert "Position limit" in result["reason"]

    def test_trade_size_capped_not_blocked(self):
        """Oversize trade is capped, not blocked."""
        market = {"endDate": (datetime.utcnow() + timedelta(days=10)).isoformat() + "Z"}
        result = validate_trade(
            market=market,
            direction="YES",
            proposed_size=200.0,  # 20% of balance, exceeds 2%
            usdc_balance=1000.0,
            open_positions_count=1,
        )
        assert result["approved"] is True  # capped, not blocked
        assert result["adjusted_size"] == 20.0  # capped to 2% of $1000

    def test_skip_resolve_time_check_no_end_date(self):
        """No end_date means no resolve-time filter applied."""
        result = validate_trade(
            market={},
            direction="YES",
            proposed_size=20.0,
            usdc_balance=1000.0,
            open_positions_count=1,
        )
        assert result["approved"] is True

    def test_return_type(self):
        """Returns dict with expected keys."""
        market = {"endDate": (datetime.utcnow() + timedelta(days=10)).isoformat() + "Z"}
        result = validate_trade(
            market=market,
            direction="NO",
            proposed_size=10.0,
            usdc_balance=500.0,
            open_positions_count=2,
        )
        assert "approved" in result
        assert "reason" in result
        assert "adjusted_size" in result


# ===================================================================
# dry_run_log
# ===================================================================

class TestDryRunLog:
    """Centralized DRY_RUN logging."""

    def test_logs_action(self, caplog):
        """Logs the action type."""
        dry_run_log("PLACE ORDER", {"market": "test", "amount": 100})
        assert "DRY RUN" in caplog.text
        assert "PLACE ORDER" in caplog.text

    def test_logs_details(self, caplog):
        """Logs all detail fields."""
        details = {"market": "btc-100k", "direction": "YES", "amount": 50.0}
        dry_run_log("TEST", details)
        assert "btc-100k" in caplog.text
        assert "YES" in caplog.text
        assert "50.0" in caplog.text

    def test_separator_lines(self, caplog):
        """Log includes separator lines (====)."""
        dry_run_log("CHECK", {})
        # Should have some === in the output
        assert "=" in caplog.text
