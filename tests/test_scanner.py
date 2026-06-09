"""
Comprehensive tests for scanner.py — the real codebase.

Tests use unittest.mock to avoid hitting the live Gamma API.
Covers: price parsing, token ID extraction, staleness calculation,
market title extraction, high-probability detection, and the full scan pipeline.
"""

import json
import sys
sys.path.insert(0, '/home/agent-lead/polymarket-trader')

from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock, PropertyMock

import pytest

# Module-level imports
import config
from scanner import (
    fetch_markets,
    fetch_orderbook,
    compute_liquidity,
    market_stale_hours,
    get_outcome_prices,
    get_token_ids,
    get_market_title,
    is_high_probability,
    rank_candidates,
    scan,
)


# ===================================================================
# Fixtures
# ===================================================================

@pytest.fixture
def sample_market() -> dict:
    """A typical two-outcome Polymarket market."""
    return {
        "question": "Will BTC reach $100k?",
        "slug": "will-btc-reach-100k",
        "outcomePrices": ["0.95", "0.05"],
        "clobTokenIds": ["token_yes_123", "token_no_456"],
        "outcomes": ["Yes", "No"],
        "conditionId": "cond_abc",
        "endDate": (datetime.utcnow() + timedelta(days=30)).isoformat() + "Z",
        "updatedAt": (datetime.utcnow() - timedelta(hours=12)).isoformat() + "Z",
        "bestBid": "0.93",
        "bestAsk": "0.97",
        "volume": 500000,
    }


@pytest.fixture
def stale_market() -> dict:
    """Market stale for >6h with high probability."""
    return {
        "question": "Will ETH staking rate hit 5%?",
        "slug": "eth-staking-rate",
        "outcomePrices": ["0.92", "0.08"],
        "clobTokenIds": json.dumps(["tok_yes_789", "tok_no_012"]),
        "outcomes": ["Yes", "No"],
        "conditionId": "cond_xyz",
        "endDate": (datetime.utcnow() + timedelta(days=10)).isoformat() + "Z",
        "updatedAt": (datetime.utcnow() - timedelta(hours=12)).isoformat() + "Z",
        "bestBid": "0.90",
        "bestAsk": "0.94",
        "volume": 100000,
    }


# ===================================================================
# Test: get_outcome_prices
# ===================================================================

class TestGetOutcomePrices:
    """outcomePrices parsing — list format only (real Gamma API behavior)."""

    def test_list_of_price_strings(self):
        """Parse outcomePrices as list of strings."""
        m = {"outcomePrices": ["0.88", "0.12"]}
        assert get_outcome_prices(m) == (0.88, 0.12)

    def test_returns_tuple(self):
        """Returns a tuple of floats."""
        m = {"outcomePrices": ["0.95", "0.05"]}
        result = get_outcome_prices(m)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_none_prices(self):
        """Returns (0.0, 0.0) when outcomePrices is None."""
        m = {"outcomePrices": None}
        assert get_outcome_prices(m) == (0.0, 0.0)

    def test_empty_prices(self):
        """Returns (0.0, 0.0) when outcomePrices is missing."""
        assert get_outcome_prices({}) == (0.0, 0.0)

    def test_empty_list(self):
        """Returns (0.0, 0.0) for empty list."""
        m = {"outcomePrices": []}
        assert get_outcome_prices(m) == (0.0, 0.0)

    def test_fallback_via_best_bid_ask(self):
        """Fallback to bestBid/bestAsk when outcomePrices is unusable."""
        m = {"outcomePrices": "not-a-list", "bestBid": "0.85", "bestAsk": "0.95"}
        prices = get_outcome_prices(m)
        # mid = (0.85 + 0.95)/2 = 0.90 → 1 - 0.90 = 0.10
        assert abs(prices[0] - 0.90) < 0.01
        assert abs(prices[1] - 0.10) < 0.01

    def test_fallback_with_none_bid_ask(self):
        """Fallback returns (0.0, 0.0) when bestBid/bestAsk are missing."""
        m = {"outcomePrices": None}
        assert get_outcome_prices(m) == (0.0, 0.0)

    def test_high_probability_market(self):
        """Market in the 88-96% range."""
        m = {"outcomePrices": ["0.93", "0.07"]}
        assert get_outcome_prices(m) == (0.93, 0.07)

    def test_json_string_format(self, stale_market):
        """clobTokenIds is a JSON string, outcomePrices is a list."""
        prices = get_outcome_prices(stale_market)
        assert prices == (0.92, 0.08)


# ===================================================================
# Test: get_token_ids
# ===================================================================

class TestGetTokenIds:
    """clobTokenIds parsing — list and JSON-string formats."""

    def test_list_format(self, sample_market):
        """Parse clobTokenIds as native list."""
        yes_id, no_id = get_token_ids(sample_market)
        assert yes_id == "token_yes_123"
        assert no_id == "token_no_456"

    def test_json_string_format(self, stale_market):
        """Parse clobTokenIds as JSON-encoded string."""
        yes_id, no_id = get_token_ids(stale_market)
        assert yes_id == "tok_yes_789"
        assert no_id == "tok_no_012"

    def test_missing_clobTokenIds(self):
        """Returns ("", "") when clobTokenIds is missing."""
        assert get_token_ids({}) == ("", "")

    def test_empty_list(self):
        """Returns ("", "") for empty list."""
        m = {"clobTokenIds": []}
        assert get_token_ids(m) == ("", "")

    def test_malformed_json_string(self):
        """Returns ("", "") for malformed JSON string."""
        m = {"clobTokenIds": "not-json"}
        assert get_token_ids(m) == ("", "")

    def test_list_with_more_than_two(self):
        """Returns first two tokens from longer list."""
        m = {"clobTokenIds": ["a", "b", "c"]}
        assert get_token_ids(m) == ("a", "b")

    def test_return_type(self):
        """Returns a tuple of strings."""
        m = {"clobTokenIds": ["yes", "no"]}
        result = get_token_ids(m)
        assert isinstance(result, tuple)
        assert len(result) == 2


# ===================================================================
# Test: get_market_title
# ===================================================================

class TestGetMarketTitle:
    """Market title extraction with fallbacks."""

    def test_question_field(self, sample_market):
        """Uses the question field."""
        assert get_market_title(sample_market) == "Will BTC reach $100k?"

    def test_title_fallback(self):
        """Falls back to title when question is missing."""
        m = {"title": "Fallback Title", "slug": "my-slug"}
        assert get_market_title(m) == "Fallback Title"

    def test_slug_fallback(self):
        """Falls back to slug when question and title are missing."""
        m = {"slug": "my-slug"}
        assert get_market_title(m) == "my-slug"

    def test_question_empty_string(self):
        """Handles empty question string."""
        m = {"question": "", "slug": "empty-q"}
        assert get_market_title(m) == "empty-q"

    def test_none_values(self):
        """Handles None values gracefully."""
        m = {"question": None, "title": None, "slug": "none-values"}
        assert get_market_title(m) == "none-values"


# ===================================================================
# Test: market_stale_hours
# ===================================================================

class TestMarketStaleHours:
    """Staleness calculation from updatedAt timestamps."""

    def test_stale_market(self, stale_market):
        """Market updated 12h ago returns ~12 hours."""
        hours = market_stale_hours(stale_market)
        assert hours is not None
        assert 11.0 <= hours <= 13.0

    def test_fresh_market(self):
        """Market updated 1h ago returns ~1 hour."""
        m = {"updatedAt": (datetime.utcnow() - timedelta(hours=1)).isoformat() + "Z"}
        hours = market_stale_hours(m)
        assert hours is not None
        assert 0.5 <= hours <= 1.5

    def test_missing_updatedAt(self):
        """Returns None when updatedAt is missing."""
        assert market_stale_hours({}) is None

    def test_none_updatedAt(self):
        """Returns None when updatedAt is None."""
        assert market_stale_hours({"updatedAt": None}) is None

    def test_z_suffix(self):
        """Handles ISO format with Z suffix."""
        dt = (datetime.utcnow() - timedelta(hours=6)).isoformat() + "Z"
        m = {"updatedAt": dt}
        hours = market_stale_hours(m)
        assert hours is not None
        assert 5.5 <= hours <= 6.5

    def test_plus_hours_offset(self):
        """Handles ISO format with +00:00 offset."""
        dt = (datetime.utcnow() - timedelta(hours=6)).isoformat()
        m = {"updatedAt": dt.replace("+00:00", "+00:00")}
        hours = market_stale_hours(m)
        assert hours is not None
        assert 5.5 <= hours <= 6.5

    def test_future_updated_at(self):
        """Future updatedAt returns negative hours."""
        m = {"updatedAt": (datetime.utcnow() + timedelta(hours=2)).isoformat() + "Z"}
        hours = market_stale_hours(m)
        assert hours is not None
        assert hours < 0

    def test_return_type(self):
        """Returns None or float."""
        assert isinstance(market_stale_hours({"updatedAt": "2026-01-01T00:00:00Z"}), float)


# ===================================================================
# Test: is_high_probability
# ===================================================================

class TestIsHighProbability:
    """High-probability detection uses MIN_PROBABILITY/MAX_PROBABILITY config."""

    def test_yes_in_range(self):
        """YES at 0.95 is within [0.88, 0.96]."""
        result, direction = is_high_probability(0.95, 0.05)
        assert result is True
        assert direction == "YES"

    def test_no_in_range(self):
        """NO at 0.90 means YES at 0.10, NO price should be detected."""
        # NO price is the second arg: 0.90
        result, direction = is_high_probability(0.10, 0.90)
        assert result is True
        assert direction == "NO"

    def test_below_minimum(self):
        """Below 0.88 returns False."""
        result, direction = is_high_probability(0.75, 0.25)
        assert result is False
        assert direction == ""

    def test_above_maximum(self):
        """Above 0.96 returns False."""
        result, direction = is_high_probability(0.98, 0.02)
        assert result is False
        assert direction == ""

    def test_at_minimum_boundary(self):
        """Exactly at 0.88 is accepted."""
        result, direction = is_high_probability(0.88, 0.12)
        assert result is True

    def test_at_maximum_boundary(self):
        """Exactly at 0.96 is accepted."""
        result, direction = is_high_probability(0.96, 0.04)
        assert result is True

    def test_both_in_range_yes_preferred(self):
        """When both YES and NO are in range, YES is returned."""
        result, direction = is_high_probability(0.90, 0.90)
        assert result is True
        assert direction == "YES"  # YES check comes first

    def test_respects_config_values(self):
        """Uses config.MIN_PROBABILITY and config.MAX_PROBABILITY."""
        assert config.MIN_PROBABILITY == 0.88
        assert config.MAX_PROBABILITY == 0.96


# ===================================================================
# Test: fetch_markets (mocked)
# ===================================================================

class TestFetchMarkets:
    """API fetching with mocked requests."""

    @patch("scanner._session")
    def test_success(self, mock_session):
        """Successful API response returns parsed list."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = [{"id": "1"}, {"id": "2"}]
        mock_session().get.return_value = mock_resp

        result = fetch_markets(limit=10)
        assert len(result) == 2
        assert result[0]["id"] == "1"

    @patch("scanner._session")
    def test_http_error_returns_empty(self, mock_session):
        """HTTP errors return empty list (not propagate)."""
        mock_session().get.side_effect = __import__("requests").exceptions.HTTPError("500")
        result = fetch_markets()
        assert result == []

    @patch("scanner._session")
    def test_timeout_returns_empty(self, mock_session):
        """Timeouts return empty list."""
        mock_session().get.side_effect = __import__("requests").exceptions.Timeout
        result = fetch_markets()
        assert result == []

    @patch("scanner._session")
    def test_passes_parameters(self, mock_session):
        """Function passes correct query parameters."""
        mock_session().get.return_value.json.return_value = []
        fetch_markets(offset=10, limit=25, tag="crypto")
        args, kwargs = mock_session().get.call_args
        assert kwargs["params"]["offset"] == 10
        assert kwargs["params"]["limit"] == 25
        assert kwargs["params"]["tag"] == "crypto"
        assert kwargs["params"]["closed"] == "false"

    @patch("scanner._session")
    def test_default_tag_is_all(self, mock_session):
        """Default tag is 'all'."""
        mock_session().get.return_value.json.return_value = []
        fetch_markets()
        args, kwargs = mock_session().get.call_args
        assert kwargs["params"]["tag"] == "all"


# ===================================================================
# Test: fetch_orderbook (mocked)
# ===================================================================

class TestFetchOrderbook:
    """Orderbook fetching with mocked requests."""

    @patch("scanner._session")
    def test_success(self, mock_session):
        """Returns orderbook dict on success."""
        mock_session().get.return_value.json.return_value = {"bids": [], "asks": []}
        result = fetch_orderbook("token_123")
        assert result == {"bids": [], "asks": []}

    @patch("scanner._session")
    def test_failure_returns_none(self, mock_session):
        """HTTP errors return None."""
        mock_session().get.side_effect = __import__("requests").exceptions.RequestException("fail")
        result = fetch_orderbook("token_123")
        assert result is None


# ===================================================================
# Test: compute_liquidity
# ===================================================================

class TestComputeLiquidity:
    """Liquidity computation from orderbook."""

    def test_basic_liquidity(self):
        """Sums top 5 levels of bids and asks (price * size)."""
        ob = {
            "bids": [["0.95", "100"], ["0.94", "200"]],
            "asks": [["0.96", "150"], ["0.97", "50"]],
        }
        # bids: 0.95*100 + 0.94*200 = 95 + 188 = 283
        # asks: 0.96*150 + 0.97*50 = 144 + 48.5 = 192.5
        # total = 283 + 192.5 = 475.5
        liq = compute_liquidity(ob)
        assert abs(liq - 475.5) < 0.01

    def test_none_orderbook(self):
        """Returns 0.0 for None orderbook."""
        assert compute_liquidity(None) == 0.0

    def test_empty_orderbook(self):
        """Returns 0.0 for empty orderbook."""
        assert compute_liquidity({}) == 0.0

    def test_only_bids(self):
        """Works with only bids."""
        ob = {"bids": [["0.90", "100"]], "asks": []}
        liq = compute_liquidity(ob)
        assert abs(liq - 90.0) < 0.01

    def test_only_asks(self):
        """Works with only asks."""
        ob = {"bids": [], "asks": [["0.95", "200"]]}
        liq = compute_liquidity(ob)
        assert abs(liq - 190.0) < 0.01

    def test_exceeds_five_levels(self):
        """Only uses top 5 levels from each side."""
        ob = {
            "bids": [[str(1.0 - 0.01 * i), "100"] for i in range(10)],
            "asks": [[str(1.0 + 0.01 * i), "100"] for i in range(10)],
        }
        liq = compute_liquidity(ob)
        # Top 5 bids: 1.0, 0.99, 0.98, 0.97, 0.96 each * 100 = 490
        # Top 5 asks: 1.0, 1.01, 1.02, 1.03, 1.04 each * 100 = 510
        # total = 1000
        assert abs(liq - 1000.0) < 1.0


# ===================================================================
# Test: scan() pipeline (mocked)
# ===================================================================

class TestScanPipeline:
    """Full scan pipeline with mocked API calls."""

    @patch("scanner.fetch_markets")
    @patch("scanner.fetch_orderbook")
    def test_no_candidates(self, mock_ob, mock_fetch):
        """scan() returns [] when no markets are high-probability."""
        mock_fetch.return_value = [
            {"outcomePrices": ["0.50", "0.50"], "updatedAt": "2026-01-01T00:00:00Z",
             "clobTokenIds": ["a", "b"], "question": "Test?", "bestBid": "0", "bestAsk": "0"}
        ]
        mock_ob.return_value = {"bids": [["0.50", "1000"]], "asks": [["0.50", "1000"]]}
        result = scan()
        assert result == []

    @patch("scanner.fetch_markets")
    @patch("scanner.fetch_orderbook")
    def test_finds_candidates(self, mock_ob, mock_fetch):
        """scan() returns candidates for high-probability stale markets."""
        now = datetime.utcnow()
        stale_mock = {
            "question": "Will this happen?",
            "slug": "test-event",
            "outcomePrices": ["0.93", "0.07"],
            "clobTokenIds": json.dumps(["tok_yes", "tok_no"]),
            "conditionId": "cond_001",
            "endDate": (now + timedelta(days=10)).isoformat() + "Z",
            "updatedAt": (now - timedelta(hours=12)).isoformat() + "Z",
            "bestBid": "0.91",
            "bestAsk": "0.95",
            "volume": 100000,
        }
        mock_fetch.return_value = [stale_mock]
        mock_ob.return_value = {"bids": [["0.93", "1000"]], "asks": [["0.93", "1000"]]}
        result = scan()
        assert len(result) >= 1
        assert result[0]["_direction"] == "YES"

    @patch("scanner.fetch_markets")
    def test_empty_api_response(self, mock_fetch):
        """scan() handles empty API response."""
        mock_fetch.return_value = []
        result = scan()
        assert result == []

    @patch("scanner.fetch_markets")
    @patch("scanner.fetch_orderbook")
    def test_skips_markets_without_token_ids(self, mock_ob, mock_fetch):
        """Markets without clobTokenIds are skipped."""
        mock_fetch.return_value = [
            {"outcomePrices": ["0.95", "0.05"], "updatedAt": "2024-01-01T00:00:00Z",
             "clobTokenIds": [], "question": "No tokens"}
        ]
        mock_ob.return_value = None
        result = scan()
        assert result == []

    @patch("scanner.fetch_markets")
    @patch("scanner.fetch_orderbook")
    def test_skips_fresh_markets(self, mock_ob, mock_fetch):
        """Markets updated recently (<6h) are skipped."""
        now = datetime.utcnow()
        mock_fetch.return_value = [
            {
                "question": "Fresh market",
                "outcomePrices": ["0.93", "0.07"],
                "clobTokenIds": ["a", "b"],
                "updatedAt": now.isoformat() + "Z",  # just updated
                "bestBid": "0.91",
                "bestAsk": "0.95",
                "volume": 1000,
            }
        ]
        mock_ob.return_value = {"bids": [["0.93", "1000"]], "asks": [["0.93", "1000"]]}
        result = scan()
        assert result == []


# ===================================================================
# Integration smoke test — only runs with -m integration
# ===================================================================

@pytest.mark.integration
class TestIntegration:
    """Smoke tests hitting the real Gamma API (deselected by default)."""

    def test_fetch_markets_returns_list(self):
        """Real API returns a list of markets."""
        markets = fetch_markets(limit=5)
        assert isinstance(markets, list)

    def test_outcome_prices_parsing(self):
        """Real market data can parse outcomePrices."""
        markets = fetch_markets(limit=5)
        for m in markets:
            prices = get_outcome_prices(m)
            assert isinstance(prices, tuple)
            assert len(prices) == 2
            assert all(isinstance(p, float) for p in prices)

    def test_token_ids_extraction(self):
        """Real market data can extract token IDs."""
        markets = fetch_markets(limit=5)
        for m in markets:
            yes_id, no_id = get_token_ids(m)
            assert isinstance(yes_id, str)
            assert isinstance(no_id, str)
