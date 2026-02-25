"""
coin_mapper.py
Maps cc_news.categories pipe-delimited tokens → DB slugs.
Uses the category values confirmed live in the DB (Feb 2026).
Read-only: never writes to any table.
"""

# Maps cc_news category tokens → 1K_coins_ohlcv slug values
# Built from live DB query on cc_news categories column
CATEGORY_TO_SLUG = {
    "BTC":        "bitcoin",
    "ETH":        "ethereum",
    "XRP":        "ripple",
    "SOL":        "solana",
    "BNB":        "binancecoin",
    "DOGE":       "dogecoin",
    "ADA":        "cardano",
    "SHIB":       "shiba-inu",
    "USDT":       "tether",
    "USDC":       "usd-coin",
    "ZRX":        "0x",
    "AVAX":       "avalanche-2",
    "DOT":        "polkadot",
    "MATIC":      "matic-network",
    "LINK":       "chainlink",
    "LTC":        "litecoin",
    "BCH":        "bitcoin-cash",
    "UNI":        "uniswap",
    "ATOM":       "cosmos",
    "XLM":        "stellar",
    "NEAR":       "near",
    "APT":        "aptos",
    "ARB":        "arbitrum",
    "OP":         "optimism",
    "SUI":        "sui",
    "TON":        "the-open-network",
    "FIL":        "filecoin",
    "ICP":        "internet-computer",
    "HBAR":       "hedera-hashgraph",
    "VET":        "vechain",
    "ALGO":       "algorand",
    "ETC":        "ethereum-classic",
    "EGLD":       "elrond-erd-2",
    "FTM":        "fantom",
    "SAND":       "the-sandbox",
    "MANA":       "decentraland",
    "AXS":        "axie-infinity",
    "THETA":      "theta-token",
    "AAVE":       "aave",
    "MKR":        "maker",
    "SNX":        "synthetix-network-token",
    "CRV":        "curve-dao-token",
    "COMP":       "compound-governance-token",
    "ENJ":        "enjincoin",
    "CHZ":        "chiliz",
    "BAT":        "basic-attention-token",
    "ZEC":        "zcash",
    "DASH":       "dash",
    "XMR":        "monero",
    "PEPE":       "pepe",
    "WIF":        "dogwifcoin",
    "BONK":       "bonk",
    "FLOKI":      "floki",
}

# Broad market categories that map to BTC as market proxy
MARKET_PROXY_CATEGORIES = {
    "CRYPTOCURRENCY", "MARKET", "TRADING", "BUSINESS",
    "MACROECONOMICS", "BLOCKCHAIN", "EXCHANGE",
    "DIGITAL ASSET TREASURY", "RESEARCH",
}

# Categories to skip — not coin-specific signals
SKIP_CATEGORIES = {
    "OTHER", "SPONSORED", "FIAT", "ASIA", "REGULATION",
    "SECURITY INCIDENTS", "MINING", "TECHNOLOGY", "WALLET",
}


def map_categories_to_slugs(categories_str: str, include_market_proxy: bool = False) -> list[str]:
    """
    Convert a cc_news categories pipe-delimited string to a list of coin slugs.

    Args:
        categories_str: e.g. "BTC|ETH|MARKET|TRADING|CRYPTOCURRENCY"
        include_market_proxy: if True, add 'bitcoin' when only broad market cats found

    Returns:
        List of slugs, deduplicated, e.g. ['bitcoin', 'ethereum']
    """
    if not categories_str:
        return []

    tokens = [t.strip().upper() for t in categories_str.split("|")]
    slugs = []

    for token in tokens:
        if token in CATEGORY_TO_SLUG:
            slug = CATEGORY_TO_SLUG[token]
            if slug not in slugs:
                slugs.append(slug)

    # If no specific coin found but article is market-wide, tag as BTC proxy
    if not slugs and include_market_proxy:
        has_market_token = any(t in MARKET_PROXY_CATEGORIES for t in tokens)
        if has_market_token:
            slugs = ["bitcoin"]

    return slugs


def get_source_tier(source_name: str) -> int:
    """
    Assign credibility tier to a news source.
    Tier 1 = premium (2x weight in quality score)
    Tier 2 = mid-tier
    Tier 3 = low-quality / high-volume farms

    Based on live source distribution from cc_news (Feb 2026).
    """
    TIER_1 = {
        "CoinDesk", "Cointelegraph", "Decrypt", "Seeking Alpha",
        "The Block", "Bloomberg Crypto", "Reuters", "Financial Times",
        "Wall Street Journal", "Forbes Crypto",
    }
    TIER_3 = {
        "Bitcoin World", "CoinOtag", "TimesTabloid", "CoinTurk News",
        "BitcoinSistemi", "Coinpaper",
    }

    if source_name in TIER_1:
        return 1
    if source_name in TIER_3:
        return 3
    return 2
