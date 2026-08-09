"""Regression tests for CP-016 B0 XRP/ripple mapping.

Covers the mapper fix in etl/transform/coin_mapper.py:
  - XRP / XRP news / XRP price -> ripple
  - mixed-title mapping (Ethereum + XRP)
  - case-insensitive matching
  - word-boundary protection (XRPL, WXRP, XRPETF, XRPINU must NOT map to ripple)
  - deterministic, duplicate-free slug output
"""
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from etl.transform.coin_mapper import map_categories_to_slugs


def _map(title: str = "", categories: str = "", body: str = ""):
    return map_categories_to_slugs(categories, include_market_proxy=True, title=title, body=body)


# ── XRP -> ripple (the B0 recall fix) ──────────────────────────────────

def test_xrp_title_maps_to_ripple():
    assert _map(title="XRP") == ["ripple"]


def test_xrp_news_title_maps_to_ripple():
    assert _map(title="XRP news: exchange listing") == ["ripple"]


def test_xrp_price_title_maps_to_ripple():
    assert _map(title="XRP price analysis") == ["ripple"]


# ── mixed-title mapping ────────────────────────────────────────────────

def test_ethereum_and_xrp_both_mapped():
    slugs = _map(title="Ethereum and XRP both rally")
    assert "ethereum" in slugs
    assert "ripple" in slugs


def test_mixed_case_xrp_maps_with_ethereum():
    slugs = _map(title="xrp gains as ethereum dips")
    assert "ripple" in slugs
    assert "ethereum" in slugs


def test_category_plus_xrp_title_merges():
    slugs = _map(title="XRP joins the rally", categories="BTC|ETH")
    assert slugs == ["bitcoin", "ethereum", "ripple"]


# ── case-insensitive matching ──────────────────────────────────────────

@pytest.mark.parametrize("title", ["XRP", "xrp", "Xrp", "xRp"])
def test_xrp_case_insensitive(title):
    assert "ripple" in _map(title=title)


# ── word-boundary protection (must NOT map to ripple) ──────────────────

@pytest.mark.parametrize("title", [
    "XRPL RWA market cap nears $55M",
    "WXRP transferred to exchange",
    "XRPETF token momentum",
    "XRPINU meme coin",
    "XRPPower staking platform",
    "RippleX announces XRPL testnet reset",
    "stXRP yield on Flare",
])
def test_xrp_variants_do_not_map_to_ripple(title):
    assert "ripple" not in _map(title=title)


def test_ripple_word_still_maps():
    assert _map(title="Ripple partnership with bank") == ["ripple"]


# ── deterministic, duplicate-free slug output ──────────────────────────

def test_output_is_deterministic():
    a = _map(title="Ethereum and XRP both rally")
    b = _map(title="Ethereum and XRP both rally")
    assert a == b


def test_output_has_no_duplicate_slugs():
    slugs = _map(title="XRP XRP XRP news XRP price")
    assert len(slugs) == len(set(slugs))
    assert slugs.count("ripple") == 1


def test_repeat_calls_do_not_accumulate():
    one = _map(title="XRP news")
    two = _map(title="XRP news")
    three = _map(title="XRP news")
    assert one == two == three == ["ripple"]
