"""Tests for news event detector."""
import numpy as np
import pandas as pd
import pytest


def test_rule_based_classifier():
    from src.features.news_events import classify_event_rule_based

    assert classify_event_rule_based("Coinbase lists XRP for trading") == "listing"
    assert classify_event_rule_based("Hacker steals $100M from DeFi protocol") == "hack_exploit"
    assert classify_event_rule_based("SEC approves Bitcoin ETF application") == "regulatory"
    assert classify_event_rule_based("Microsoft partners with Ethereum Foundation") == "partnership"
    assert classify_event_rule_based("Token burn event removes 10% of supply") == "tokenomics"
    assert classify_event_rule_based("Federal Reserve raises interest rates") == "macro"
    assert classify_event_rule_based("Bitcoin price moved today") == "neutral"


def test_compute_hours_since():
    from src.features.news_events import compute_hours_since

    events = pd.DataFrame({
        "timestamp": pd.to_datetime(["2025-06-01 10:00+00:00", "2025-06-02 15:00+00:00"]),
        "event_type": ["listing", "hack_exploit"],
    })
    current = pd.Timestamp("2025-06-03 10:00+00:00")
    result = compute_hours_since(events, current)

    assert abs(result["hours_since_listing"] - 48.0) < 0.1
    assert abs(result["hours_since_hack_exploit"] - 19.0) < 0.1
    assert result["hours_since_regulatory"] is None


def test_magnitude_lookup():
    from src.features.news_events import get_magnitude_estimate

    assert get_magnitude_estimate("listing") > 0
    assert get_magnitude_estimate("hack_exploit") < 0
    assert abs(get_magnitude_estimate("neutral")) < 0.01
