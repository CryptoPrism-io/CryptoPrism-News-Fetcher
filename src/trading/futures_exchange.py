"""
futures_exchange.py
Binance USDM Futures exchange wrapper using ccxt.
Used for the short leg of the market-neutral portfolio.
1x leverage only — no actual leverage, just directional shorting.

Config via .env:
    BINANCE_FUTURES_API_KEY=...
    BINANCE_FUTURES_SECRET=...
    BINANCE_TESTNET=true  (default)
"""

import logging
import os

import ccxt
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

# Same slug mapping as spot — futures uses same symbols with :USDT suffix
SLUG_TO_FUTURES_SYMBOL = {
    "bitcoin": "BTC/USDT:USDT",
    "ethereum": "ETH/USDT:USDT",
    "solana": "SOL/USDT:USDT",
    "xrp": "XRP/USDT:USDT",
    "bnb": "BNB/USDT:USDT",
    "dogecoin": "DOGE/USDT:USDT",
    "cardano": "ADA/USDT:USDT",
    "chainlink": "LINK/USDT:USDT",
    "avalanche": "AVAX/USDT:USDT",
    "litecoin": "LTC/USDT:USDT",
    "stellar": "XLM/USDT:USDT",
    "bitcoin-cash": "BCH/USDT:USDT",
    "uniswap": "UNI/USDT:USDT",
    "cosmos": "ATOM/USDT:USDT",
    "hedera": "HBAR/USDT:USDT",
    "sui": "SUI/USDT:USDT",
    "tron": "TRX/USDT:USDT",
    "zcash": "ZEC/USDT:USDT",
    "aave": "AAVE/USDT:USDT",
    "algorand": "ALGO/USDT:USDT",
    "arbitrum": "ARB/USDT:USDT",
    "aptos": "APT/USDT:USDT",
    "near": "NEAR/USDT:USDT",
    "filecoin": "FIL/USDT:USDT",
    "internet-computer": "ICP/USDT:USDT",
    "polkadot": "DOT/USDT:USDT",
    "optimism": "OP/USDT:USDT",
    "pepe": "PEPE/USDT:USDT",
    "ethereum-classic": "ETC/USDT:USDT",
    "the-sandbox": "SAND/USDT:USDT",
    "decentraland": "MANA/USDT:USDT",
    "axie-infinity": "AXS/USDT:USDT",
    "chiliz": "CHZ/USDT:USDT",
}


def build_futures_exchange() -> ccxt.binanceusdm:
    """Build Binance USDM Futures exchange client for testnet."""
    testnet = os.getenv("BINANCE_TESTNET", "true").lower() == "true"

    config = {
        "apiKey": os.getenv("BINANCE_FUTURES_API_KEY", ""),
        "secret": os.getenv("BINANCE_FUTURES_SECRET", ""),
        "options": {
            "defaultType": "future",
            "fetchCurrencies": False,
        },
        "enableRateLimit": True,
    }

    exchange = ccxt.binanceusdm(config)

    if testnet:
        exchange.set_sandbox_mode(True)

    mode = "TESTNET" if testnet else "LIVE"
    log.info(f"Binance Futures {mode} connected")

    try:
        exchange.load_markets()
        balance = exchange.fetch_balance()
        usdt = float(balance.get("USDT", {}).get("free", 0))
        log.info(f"Futures USDT balance: {usdt:.2f}")
    except Exception as e:
        log.warning(f"Could not fetch futures balance: {e}")

    return exchange


def set_leverage(exchange: ccxt.binanceusdm, symbol: str, leverage: int = 1):
    """Set leverage to 1x (no actual leverage) and isolated margin."""
    try:
        exchange.set_margin_mode("isolated", symbol)
    except Exception:
        pass  # may already be set
    try:
        exchange.set_leverage(leverage, symbol)
    except Exception:
        pass  # may already be set


def get_futures_price(exchange: ccxt.binanceusdm, symbol: str) -> float:
    """Get current futures mark price."""
    ticker = exchange.fetch_ticker(symbol)
    return float(ticker["last"])


def open_short(exchange: ccxt.binanceusdm, symbol: str, usdt_amount: float) -> dict:
    """
    Open a short position using market order.
    1x leverage, isolated margin.
    Returns: {symbol, side, qty, price, cost}
    """
    set_leverage(exchange, symbol, 1)

    price = get_futures_price(exchange, symbol)
    qty = usdt_amount / price
    qty = exchange.amount_to_precision(symbol, qty)

    log.info(f"SHORT {symbol}: {qty} @ ~{price:.4f} (${usdt_amount:.2f})")

    order = exchange.create_market_sell_order(symbol, float(qty))

    filled_qty = float(order.get("filled", qty))
    avg_price = float(order.get("average", price))
    cost = filled_qty * avg_price

    log.info(f"  Filled: {filled_qty} @ {avg_price:.4f} = ${cost:.2f}")

    return {
        "symbol": symbol,
        "side": "SHORT",
        "qty": filled_qty,
        "price": avg_price,
        "cost": cost,
        "order_id": order.get("id"),
    }


def close_short(exchange: ccxt.binanceusdm, symbol: str, qty: float) -> dict:
    """
    Close a short position by buying back.
    Returns: {symbol, side, qty, price, cost}
    """
    qty = exchange.amount_to_precision(symbol, qty)
    price = get_futures_price(exchange, symbol)

    log.info(f"CLOSE SHORT {symbol}: {qty} @ ~{price:.4f}")

    order = exchange.create_market_buy_order(symbol, float(qty), {"reduceOnly": True})

    filled_qty = float(order.get("filled", qty))
    avg_price = float(order.get("average", price))
    cost = filled_qty * avg_price

    log.info(f"  Filled: {filled_qty} @ {avg_price:.4f} = ${cost:.2f}")

    return {
        "symbol": symbol,
        "side": "BUY",
        "qty": filled_qty,
        "price": avg_price,
        "cost": cost,
        "order_id": order.get("id"),
    }


def get_futures_balance(exchange: ccxt.binanceusdm) -> dict:
    """Get futures USDT balance."""
    balance = exchange.fetch_balance()
    return {
        "usdt_free": float(balance.get("USDT", {}).get("free", 0)),
        "usdt_total": float(balance.get("USDT", {}).get("total", 0)),
    }


def get_open_futures_positions(exchange: ccxt.binanceusdm) -> list[dict]:
    """Get all open futures positions from exchange."""
    positions = exchange.fetch_positions()
    return [
        {
            "symbol": p["symbol"],
            "side": "SHORT" if float(p.get("contracts", 0)) < 0 or p.get("side") == "short" else "LONG",
            "qty": abs(float(p.get("contracts", 0))),
            "entry_price": float(p.get("entryPrice", 0)),
            "unrealized_pnl": float(p.get("unrealizedPnl", 0)),
            "mark_price": float(p.get("markPrice", 0)),
        }
        for p in positions
        if abs(float(p.get("contracts", 0))) > 0
    ]


def slug_to_futures_symbol(slug: str) -> str | None:
    """Convert DB slug to Binance futures trading pair."""
    return SLUG_TO_FUTURES_SYMBOL.get(slug)
