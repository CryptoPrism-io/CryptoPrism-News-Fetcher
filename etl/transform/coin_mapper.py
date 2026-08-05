"""
coin_mapper.py
Maps cc_news.categories pipe-delimited tokens → DB slugs.
Also provides title-based fuzzy matching for broader coverage.
Read-only: never writes to any table.
"""

import re

# Maps cc_news category tokens → 1K_coins_ohlcv slug values
# Expanded April 2026 to cover top 150+ coins by market cap
CATEGORY_TO_SLUG = {
    # Top 50
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
    "TRX":        "tron",
    "LEO":        "leo-token",
    "DAI":        "dai",
    "RENDER":     "render-token",
    "KAS":        "kaspa",
    "IMX":        "immutable-x",
    "INJ":        "injective-protocol",
    "SEI":        "sei-network",
    "STX":        "blockstack",
    "TIA":        "celestia",
    "JUP":        "jupiter-exchange-solana",
    "FET":        "fetch-ai",
    "GRT":        "the-graph",
    "RNDR":       "render-token",
    "RUNE":       "thorchain",
    "MNT":        "mantle",
    "BEAM":       "beam",
    "WLD":        "worldcoin-wld",
    "PYTH":       "pyth-network",
    # 51-100
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
    "EGLD":       "elrond-erd-2",
    "ZRX":        "0x",
    "LDO":        "lido-dao",
    "RPL":        "rocket-pool",
    "PENDLE":     "pendle",
    "DYDX":       "dydx",
    "GMX":        "gmx",
    "BLUR":       "blur",
    "LOOM":       "loom-network-new",
    "1INCH":      "1inch",
    "SUSHI":      "sushi",
    "YFI":        "yearn-finance",
    "QNT":        "quant-network",
    "GALA":       "gala",
    "FLOW":       "flow",
    "KAVA":       "kava",
    "ROSE":       "oasis-network",
    "ZIL":        "zilliqa",
    "ONE":        "harmony",
    "CELO":       "celo",
    "SKL":        "skale",
    "ANKR":       "ankr",
    "STORJ":      "storj",
    "OCEAN":      "ocean-protocol",
    "MASK":       "mask-network",
    "API3":       "api3",
    "SSV":        "ssv-network",
    "ACH":        "alchemy-pay",
    "AGIX":       "singularitynet",
    "AR":         "arweave",
    "MINA":       "mina-protocol",
    "CFX":        "conflux-token",
    # 101-150+
    "ONDO":       "ondo-finance",
    "ENA":        "ethena",
    "W":          "wormhole",
    "JTO":        "jito-governance-token",
    "DYM":        "dymension",
    "STRK":       "starknet",
    "ORDI":       "ordinals",
    "SATS":       "1000sats",
    "TAO":        "bittensor",
    "AKT":        "akash-network",
    "NTRN":       "neutron-3",
    "TWT":        "trust-wallet-token",
    "XDC":        "xdce-crowd-sale",
    "NEO":        "neo",
    "IOTA":       "iota",
    "XTZ":        "tezos",
    "EOS":        "eos",
    "WAVES":      "waves",
    "KSM":        "kusama",
    "QTUM":       "qtum",
    "ICX":        "icon",
    "OMG":        "omisego",
    "ZEN":        "zencash",
    "SC":         "siacoin",
    "RVN":        "ravencoin",
    "ONT":        "ontology",
    "IOTX":       "iotex",
    "AUDIO":      "audius",
    "JASMY":      "jasmycoin",
    "CKB":        "nervos-network",
    "SUPER":      "superfarm",
    "PIXEL":      "pixels",
    "PORTAL":     "portal",
    "RONIN":      "ronin",
    "AXL":        "axelar",
    "ALT":        "altlayer",
    "MANTA":      "manta-network",
    "ASTR":       "astar",
    "GLMR":       "moonbeam",
    "MOVR":       "moonriver",
}

# Title-based name matching for coins not in categories.
# Maps lowercase name → slug for fuzzy title/body matching.
TITLE_NAME_TO_SLUG = {
    "bitcoin": "bitcoin", "ethereum": "ethereum", "solana": "solana",
    "cardano": "cardano", "polkadot": "polkadot", "chainlink": "chainlink",
    "avalanche": "avalanche-2", "dogecoin": "dogecoin", "shiba inu": "shiba-inu",
    "ripple": "ripple", "litecoin": "litecoin", "uniswap": "uniswap",
    "cosmos": "cosmos", "stellar": "stellar", "filecoin": "filecoin",
    "aptos": "aptos", "arbitrum": "arbitrum", "optimism": "optimism",
    "toncoin": "the-open-network", "hedera": "hedera-hashgraph",
    "vechain": "vechain", "algorand": "algorand", "fantom": "fantom",
    "the sandbox": "the-sandbox", "decentraland": "decentraland",
    "aave": "aave", "maker": "maker", "compound": "compound-governance-token",
    "zcash": "zcash", "monero": "monero", "tron": "tron",
    "kaspa": "kaspa", "injective": "injective-protocol", "celestia": "celestia",
    "worldcoin": "worldcoin-wld", "render": "render-token",
    "thorchain": "thorchain", "arweave": "arweave", "bittensor": "bittensor",
    "starknet": "starknet", "ordinals": "ordinals", "tezos": "tezos",
    "iota": "iota", "neo": "neo", "eos": "eos", "waves": "waves",
    "kusama": "kusama", "pendle": "pendle", "dydx": "dydx",
    "lido": "lido-dao", "blur": "blur", "gala": "gala",
    "flow": "flow", "mina": "mina-protocol", "ocean protocol": "ocean-protocol",
    "ethena": "ethena", "ondo": "ondo-finance", "jupiter": "jupiter-exchange-solana",
    "fetch.ai": "fetch-ai", "the graph": "the-graph",
    "immutable": "immutable-x", "mantle": "mantle", "pyth": "pyth-network",
}

_TITLE_PATTERNS = {
    slug: re.compile(r'\b' + re.escape(name) + r'\b', re.IGNORECASE)
    for name, slug in TITLE_NAME_TO_SLUG.items()
    if len(name) > 3  # skip short names to avoid false positives
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


def map_categories_to_slugs(categories_str: str, include_market_proxy: bool = False,
                            title: str = "", body: str = "") -> list[str]:
    """
    Convert a cc_news categories pipe-delimited string to a list of coin slugs.
    Falls back to title/body matching for broader coverage.

    Args:
        categories_str: e.g. "BTC|ETH|MARKET|TRADING|CRYPTOCURRENCY"
        include_market_proxy: if True, add 'bitcoin' when only broad market cats found
        title: article title for fuzzy matching
        body: article body for fuzzy matching (first 500 chars used)

    Returns:
        List of slugs, deduplicated, e.g. ['bitcoin', 'ethereum']
    """
    if not categories_str:
        categories_str = ""

    tokens = [t.strip().upper() for t in categories_str.split("|") if t.strip()]
    slugs = []

    for token in tokens:
        if token in CATEGORY_TO_SLUG:
            slug = CATEGORY_TO_SLUG[token]
            if slug not in slugs:
                slugs.append(slug)

    # Title/body matching for additional coverage
    search_text = f"{title} {body[:500] if body else ''}"
    if search_text.strip():
        for slug, pattern in _TITLE_PATTERNS.items():
            if slug not in slugs and pattern.search(search_text):
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
