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

# Slug → Binance Futures USDC testnet symbol (verified available April 2026)
SLUG_TO_FUTURES_SYMBOL = {
    "bitcoin":       "BTC/USDC:USDC",
    "ethereum":      "ETH/USDC:USDC",
    "solana":        "SOL/USDC:USDC",
    "xrp":           "XRP/USDC:USDC",
    "bnb":           "BNB/USDC:USDC",
    "dogecoin":      "DOGE/USDC:USDC",
    "cardano":       "ADA/USDC:USDC",
    "chainlink":     "LINK/USDC:USDC",
    "avalanche-2":   "AVAX/USDC:USDC",
    "litecoin":      "LTC/USDC:USDC",
    "bitcoin-cash":  "BCH/USDC:USDC",
    "uniswap":       "UNI/USDC:USDC",
    "hedera-hashgraph": "HBAR/USDC:USDC",
    "sui":           "SUI/USDC:USDC",
    "zcash":         "ZEC/USDC:USDC",
    "aave":          "AAVE/USDC:USDC",
    "arbitrum":      "ARB/USDC:USDC",
    "near":          "NEAR/USDC:USDC",
    "filecoin":      "FIL/USDC:USDC",
    "neo":           "NEO/USDC:USDC",
    "curve-dao-token": "CRV/USDC:USDC",
    "ethena":        "ENA/USDC:USDC",
    "celestia":      "TIA/USDC:USDC",
    "worldcoin-wld": "WLD/USDC:USDC",
    "dogwifcoin":    "WIF/USDC:USDC",
    "bonk":          "1000BONK/USDC:USDC",
    "pepe":          "1000PEPE/USDC:USDC",
    "shiba-inu":     "1000SHIB/USDC:USDC",
    "ordinals":      "ORDI/USDC:USDC",
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
        # ccxt 4.5+ deprecated set_sandbox_mode for futures — use demo trading URLs
        try:
            exchange.set_sandbox_mode(True)
        except Exception:
            pass
        # Override URLs to futures testnet directly
        exchange.urls["api"] = {
            "fapiPublic": "https://testnet.binancefuture.com/fapi/v1",
            "fapiPublicV2": "https://testnet.binancefuture.com/fapi/v2",
            "fapiPublicV3": "https://testnet.binancefuture.com/fapi/v3",
            "fapiPrivate": "https://testnet.binancefuture.com/fapi/v1",
            "fapiPrivateV2": "https://testnet.binancefuture.com/fapi/v2",
            "fapiPrivateV3": "https://testnet.binancefuture.com/fapi/v3",
            "public": "https://testnet.binancefuture.com/fapi/v1",
            "private": "https://testnet.binancefuture.com/fapi/v1",
        }

    mode = "TESTNET" if testnet else "LIVE"
    log.info(f"Binance Futures {mode} connected")

    try:
        exchange.load_markets()
        balance = exchange.fetch_balance()
        usdt = float(balance.get("USDC", {}).get("free", 0))
        log.info(f"Futures USDT balance: {usdt:.2f}")
    except Exception as e:
        log.warning(f"Could not fetch futures balance: {e}")

    return exchange


def set_leverage(exchange: ccxt.binanceusdm, symbol: str, leverage: int = 2):
    """Set leverage and isolated margin. Default 2x to match spot long notional."""
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


def open_long(exchange: ccxt.binanceusdm, symbol: str, usdc_amount: float) -> dict:
    """
    Open a long position using market order.
    1x leverage, isolated margin.
    Returns: {symbol, side, qty, price, cost}
    """
    set_leverage(exchange, symbol, 1)

    price = get_futures_price(exchange, symbol)
    qty = usdc_amount / price
    qty = exchange.amount_to_precision(symbol, qty)

    log.info(f"LONG {symbol}: {qty} @ ~{price:.4f} (${usdc_amount:.2f})")

    order = exchange.create_market_buy_order(symbol, float(qty))

    filled_qty = float(order.get("filled", qty))
    avg_price = float(order.get("average", price))
    cost = filled_qty * avg_price

    log.info(f"  Filled: {filled_qty} @ {avg_price:.4f} = ${cost:.2f}")

    return {
        "symbol": symbol,
        "side": "LONG",
        "qty": filled_qty,
        "price": avg_price,
        "cost": cost,
        "order_id": order.get("id"),
    }


def close_long(exchange: ccxt.binanceusdm, symbol: str, qty: float) -> dict:
    """
    Close a long position by selling.
    Returns: {symbol, side, qty, price, cost}
    """
    qty = exchange.amount_to_precision(symbol, qty)
    price = get_futures_price(exchange, symbol)

    log.info(f"CLOSE LONG {symbol}: {qty} @ ~{price:.4f}")

    order = exchange.create_market_sell_order(symbol, float(qty), {"reduceOnly": True})

    filled_qty = float(order.get("filled", qty))
    avg_price = float(order.get("average", price))
    cost = filled_qty * avg_price

    log.info(f"  Filled: {filled_qty} @ {avg_price:.4f} = ${cost:.2f}")

    return {
        "symbol": symbol,
        "side": "SELL",
        "qty": filled_qty,
        "price": avg_price,
        "cost": cost,
        "order_id": order.get("id"),
    }


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
        "usdt_free": float(balance.get("USDC", {}).get("free", 0)),
        "usdt_total": float(balance.get("USDC", {}).get("total", 0)),
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
