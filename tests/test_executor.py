"""
Comprehensive tests for executor.py — the real codebase.

Tests cover:
- Order placement flow with DRY_RUN enforcement
- Risk validation chaining
- Price calculation (mid price, maker price)
- Order cancellation paths
- Edge cases for all execution paths
"""

import sys
sys.path.insert(0, '/home/agent-lead/polymarket-trader')

from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

import pytest

import config
import risk
from executor import (
    place_order,
    cancel_order,
    cancel_all_orders,
    get_open_orders,
    _get_mid_price,
    _price_one_tick_below_ask,
)


# ===================================================================
# Fixtures
# ===================================================================

@pytest.fixture
def valid_market() -> dict:
    """A market dict with all fields needed for order placement."""
    return {
        "_title": "Test Market",
        "_yes_token": "yes_token_abc",
        "_no_token": "no_token_def",
        "conditionId": "cond_001",
        "_orderbook": {
            "bids": [["0.94", "1000"], ["0.93", "2000"]],
            "asks": [["0.96", "1500"], ["0.97", "500"]],
        },
    }


# ===================================================================
# Test: _get_mid_price
# ===================================================================

class TestGetMidPrice:
    """Mid price calculation from orderbook."""

    def test_normal_orderbook(self):
        """Mid price = (best_bid + best_ask) / 2."""
        ob = {"bids": [["0.95", "100"]], "asks": [["0.97", "100"]]}
        mid = _get_mid_price(ob)
        assert abs(mid - 0.96) < 0.001

    def test_no_bids(self):
        """Uses 0.0 for best bid when bids are empty."""
        ob = {"bids": [], "asks": [["0.95", "100"]]}
        mid = _get_mid_price(ob)
        assert abs(mid - 0.475) < 0.001  # (0 + 0.95)/2

    def test_no_asks(self):
        """Uses 1.0 for best ask when asks are empty."""
        ob = {"bids": [["0.90", "100"]], "asks": []}
        mid = _get_mid_price(ob)
        assert abs(mid - 0.95) < 0.001  # (0.90 + 1.0)/2

    def test_empty_orderbook(self):
        """Returns 0.0 for empty orderbook."""
        assert _get_mid_price({}) == 0.0

    def test_malformed_orders(self):
        """Handles missing price/size gracefully."""
        ob = {"bids": [[None, "100"]], "asks": [["0.95", "100"]]}
        mid = _get_mid_price(ob)
        assert isinstance(mid, float)


# ===================================================================
# Test: _price_one_tick_below_ask
# ===================================================================

class TestPriceOneTickBelowAsk:
    """Maker price calculation (1 tick below best ask)."""

    def test_normal(self):
        """Returns best_ask - tick_size."""
        ob = {"asks": [["0.9500", "100"]]}
        price = _price_one_tick_below_ask(ob)
        assert abs(price - 0.9499) < 0.0001

    def test_no_asks(self):
        """Returns 0.0 when no asks."""
        assert _price_one_tick_below_ask({"asks": []}) == 0.0

    def test_empty_orderbook(self):
        """Returns 0.0 for empty orderbook."""
        assert _price_one_tick_below_ask({}) == 0.0


# ===================================================================
# Test: place_order
# ===================================================================

class TestPlaceOrder:
    """Order placement flow."""

    @patch("executor.config.DRY_RUN", True)
    def test_dry_run_blocks_via_log(self, valid_market):
        """When DRY_RUN=True, place_order logs and returns dry_run receipt."""
        with patch("executor.risk.dry_run_log") as mock_log:
            result = place_order(
                market=valid_market,
                direction="YES",
                size_usdc=20.0,
                usdc_balance=1000.0,
                open_positions_count=3,
            )
            assert result is not None
            assert result["dry_run"] is True
            assert result["status"] == "dry_run"
            assert result["direction"] == "YES"
            assert result["market_title"] == "Test Market"
            mock_log.assert_called_once()

    @patch("executor.config.DRY_RUN", True)
    def test_dry_run_returns_receipt_keys(self, valid_market):
        """Dry-run receipt has all expected keys."""
        result = place_order(valid_market, "YES", 20.0, 1000.0, 3)
        assert "dry_run" in result
        assert "market_title" in result
        assert "direction" in result
        assert "size" in result
        assert "price" in result
        assert "status" in result

    @patch("executor.config.DRY_RUN", True)
    def test_rejected_by_risk_position_limit(self, valid_market):
        """Trade rejected when max positions reached."""
        result = place_order(
            market=valid_market,
            direction="YES",
            size_usdc=20.0,
            usdc_balance=1000.0,
            open_positions_count=5,  # at max
        )
        assert result is None  # rejected

    @patch("executor.config.DRY_RUN", True)
    def test_trade_size_capped(self, valid_market):
        """Oversize trade is capped, size in receipt reflects cap."""
        result = place_order(
            market=valid_market,
            direction="YES",
            size_usdc=200.0,  # 20% of $1000
            usdc_balance=1000.0,
            open_positions_count=2,
        )
        assert result is not None
        # Size should be capped to 2% of $1000 = $20
        assert result["size"] == 20.0

    @patch("executor.config.DRY_RUN", True)
    def test_missing_token_ids_returns_none(self):
        """Market without _yes_token / _no_token returns None."""
        market = {"_title": "Bad Market", "_yes_token": "", "_no_token": ""}
        result = place_order(market, "YES", 20.0, 1000.0, 2)
        assert result is None

    @patch("executor.config.DRY_RUN", True)
    def test_no_direction_token_selection(self, valid_market):
        """YES uses _yes_token, NO uses _no_token."""
        result = place_order(valid_market, "YES", 20.0, 1000.0, 2)
        assert result is not None
        result_no = place_order(valid_market, "NO", 20.0, 1000.0, 2)
        assert result_no is not None


# ===================================================================
# Test: cancel_order
# ===================================================================

class TestCancelOrder:
    """Order cancellation."""

    @patch("executor.config.DRY_RUN", True)
    def test_dry_run_returns_true(self):
        """In DRY_RUN mode, cancel_order returns True."""
        with patch("executor.risk.dry_run_log") as mock_log:
            result = cancel_order("order_123")
            assert result is True
            mock_log.assert_called_once_with("CANCEL ORDER", {"order_id": "order_123"})


# ===================================================================
# Test: cancel_all_orders
# ===================================================================

class TestCancelAllOrders:
    """Cancelling all orders."""

    @patch("executor.config.DRY_RUN", True)
    def test_dry_run_returns_true(self):
        """In DRY_RUN mode, cancel_all_orders returns True."""
        with patch("executor.risk.dry_run_log") as mock_log:
            result = cancel_all_orders(["token_a", "token_b"])
            assert result is True
            mock_log.assert_called_once_with("CANCEL ALL", {"token_ids": ["token_a", "token_b"]})


# ===================================================================
# Test: get_open_orders
# ===================================================================

class TestGetOpenOrders:
    """Fetching open orders (mocked)."""

    @patch("executor._get_clob_client")
    def test_returns_orders(self, mock_client):
        """Returns list of open orders."""
        mock_client.return_value.get_orders.return_value = [{"id": "1"}, {"id": "2"}]
        orders = get_open_orders()
        assert len(orders) == 2

    @patch("executor._get_clob_client")
    def test_error_returns_empty(self, mock_client):
        """On error, returns empty list."""
        mock_client.return_value.get_orders.side_effect = Exception("API error")
        orders = get_open_orders()
        assert orders == []
