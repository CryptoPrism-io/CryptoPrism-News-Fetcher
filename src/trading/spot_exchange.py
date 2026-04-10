"""
spot_exchange.py
Binance Spot exchange wrapper using ccxt.
Supports testnet and live endpoints.

Config via .env:
    BINANCE_API_KEY=...
    BINANCE_SECRET=...
    BINANCE_TESTNET=true  (default)
"""

import logging
import os

import ccxt
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

# Slug → Binance symbol mapping (top coins)
SLUG_TO_SYMBOL = {
    "bitcoin": "BTC/USDT",
    "ethereum": "ETH/USDT",
    "solana": "SOL/USDT",
    "xrp": "XRP/USDT",
    "bnb": "BNB/USDT",
    "dogecoin": "DOGE/USDT",
    "cardano": "ADA/USDT",
    "chainlink": "LINK/USDT",
    "avalanche": "AVAX/USDT",
    "litecoin": "LTC/USDT",
    "stellar": "XLM/USDT",
    "monero": "XMR/USDT",
    "bitcoin-cash": "BCH/USDT",
    "uniswap": "UNI/USDT",
    "cosmos": "ATOM/USDT",
    "hedera": "HBAR/USDT",
    "sui": "SUI/USDT",
    "tron": "TRX/USDT",
    "zcash": "ZEC/USDT",
    "aave": "AAVE/USDT",
    "algorand": "ALGO/USDT",
    "arbitrum": "ARB/USDT",
    "aptos": "APT/USDT",
    "near": "NEAR/USDT",
    "filecoin": "FIL/USDT",
    "internet-computer": "ICP/USDT",
    "polkadot": "DOT/USDT",
    "optimism": "OP/USDT",
    "pepe": "PEPE/USDT",
    "shiba-inu": "SHIB/USDT",
    "ethereum-classic": "ETC/USDT",
    "the-sandbox": "SAND/USDT",
    "decentraland": "MANA/USDT",
    "axie-infinity": "AXS/USDT",
    "chiliz": "CHZ/USDT",
}


def build_exchange() -> ccxt.binance:
    """Build Binance spot exchange client."""
    testnet = os.getenv("BINANCE_TESTNET", "true").lower() == "true"

    exchange = ccxt.binance({
        "apiKey": os.getenv("BINANCE_API_KEY", ""),
        "secret": os.getenv("BINANCE_SECRET", ""),
        "sandbox": testnet,
        "options": {"defaultType": "spot"},
        "enableRateLimit": True,
    })

    mode = "TESTNET" if testnet else "LIVE"
    log.info(f"Binance Spot {mode} connected")

    try:
        exchange.load_markets()
        balance = exchange.fetch_balance()
        usdt = float(balance.get("USDT", {}).get("free", 0))
        log.info(f"USDT balance: {usdt:.2f}")
    except Exception as e:
        log.warning(f"Could not fetch balance: {e}")

    return exchange


def get_price(exchange: ccxt.binance, symbol: str) -> float:
    """Get current market price."""
    ticker = exchange.fetch_ticker(symbol)
    return float(ticker["last"])


def buy_market(exchange: ccxt.binance, symbol: str, usdt_amount: float) -> dict:
    """
    Place a market BUY order using USDT notional.
    Returns: {symbol, side, qty, price, cost}
    """
    price = get_price(exchange, symbol)
    qty = usdt_amount / price

    # Round to exchange precision
    market = exchange.market(symbol)
    qty = exchange.amount_to_precision(symbol, qty)

    log.info(f"BUY {symbol}: {qty} @ ~{price:.4f} (${usdt_amount:.2f})")

    order = exchange.create_market_buy_order(symbol, float(qty))

    filled_qty = float(order.get("filled", qty))
    avg_price = float(order.get("average", price))
    cost = float(order.get("cost", filled_qty * avg_price))

    log.info(f"  Filled: {filled_qty} @ {avg_price:.4f} = ${cost:.2f}")

    return {
        "symbol": symbol,
        "side": "BUY",
        "qty": filled_qty,
        "price": avg_price,
        "cost": cost,
        "order_id": order.get("id"),
    }


def sell_market(exchange: ccxt.binance, symbol: str, qty: float) -> dict:
    """
    Place a market SELL order.
    Returns: {symbol, side, qty, price, proceeds}
    """
    qty = exchange.amount_to_precision(symbol, qty)
    price = get_price(exchange, symbol)

    log.info(f"SELL {symbol}: {qty} @ ~{price:.4f}")

    order = exchange.create_market_sell_order(symbol, float(qty))

    filled_qty = float(order.get("filled", qty))
    avg_price = float(order.get("average", price))
    proceeds = float(order.get("cost", filled_qty * avg_price))

    log.info(f"  Filled: {filled_qty} @ {avg_price:.4f} = ${proceeds:.2f}")

    return {
        "symbol": symbol,
        "side": "SELL",
        "qty": filled_qty,
        "price": avg_price,
        "proceeds": proceeds,
        "order_id": order.get("id"),
    }


def get_balance(exchange: ccxt.binance) -> dict:
    """Get USDT and all non-zero asset balances."""
    balance = exchange.fetch_balance()
    result = {"usdt_free": float(balance.get("USDT", {}).get("free", 0))}

    for asset, info in balance.get("total", {}).items():
        if float(info) > 0 and asset != "USDT":
            result[asset] = {
                "total": float(info),
                "free": float(balance.get(asset, {}).get("free", 0)),
            }

    return result


def slug_to_symbol(slug: str) -> str | None:
    """Convert DB slug to Binance trading pair."""
    return SLUG_TO_SYMBOL.get(slug)
