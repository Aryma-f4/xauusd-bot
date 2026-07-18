#!/usr/bin/env python3
"""
XAUUSD Trading Bot — Bybit Perpetual
--------------------------------------
Managed by Claude Code. Aggressive strategy, $100 account.
Hybrid (Option C): bot executes rules, Claude reviews daily.

Strategy: EMA9/21 crossover on 5-min candles + RSI confirmation
Risk: 10% per trade, 5 trades/day max, 20% daily loss cap
"""

import os, sys, json, time, logging, traceback
from datetime import datetime, timezone
from typing import Optional

from pybit.unified_trading import HTTP
import pandas as pd
import numpy as np
from ta.trend import EMAIndicator
from ta.momentum import RSIIndicator

# ── Telegram (optional) ──────────────────────────────────────────
try:
    from telegram import Bot as TelegramBot
    HAS_TELEGRAM = True
except Exception:
    HAS_TELEGRAM = False

# ── Logging ───────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bot")


# ── Config ────────────────────────────────────────────────────────
class Config:
    def __init__(self):
        self.BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", "")
        self.BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", "")
        self.USE_TESTNET = os.getenv("USE_TESTNET", "true").lower() == "true"

        self.SYMBOL = os.getenv("SYMBOL", "XAUUSD")
        self.CATEGORY = os.getenv("CATEGORY", "linear")
        self.LEVERAGE = int(os.getenv("LEVERAGE", "1"))
        self.ACCOUNT_BALANCE = float(os.getenv("ACCOUNT_BALANCE", "100"))
        self.RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", "10"))
        self.MAX_TRADES_PER_DAY = int(os.getenv("MAX_TRADES_PER_DAY", "5"))
        self.MAX_DAILY_LOSS_PCT = float(os.getenv("MAX_DAILY_LOSS_PCT", "20"))
        self.CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "300"))

        self.TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
        self.TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

        self.DATA_DIR = os.getenv("DATA_DIR", "/app/data")
        self.TRADE_LOG = os.path.join(self.DATA_DIR, "trades.jsonl")
        self.STATE_FILE = os.path.join(self.DATA_DIR, "bot_state.json")


# ── Bybit Client ──────────────────────────────────────────────────
class BybitClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.session = HTTP(
            testnet=cfg.USE_TESTNET,
            api_key=cfg.BYBIT_API_KEY,
            api_secret=cfg.BYBIT_API_SECRET,
        )
        self._contract = None
        self._init_session()

    def _init_session(self):
        """Set leverage on startup. Best effort — may fail on testnet."""
        try:
            self.session.set_leverage(
                category=self.cfg.CATEGORY,
                symbol=self.cfg.SYMBOL,
                buyLeverage=str(self.cfg.LEVERAGE),
                sellLeverage=str(self.cfg.LEVERAGE),
            )
        except Exception as e:
            log.warning("Leverage init (may fail on testnet): %s", e)

    # ── helpers ───────────────────────────────────────────────

    def _r(self, method, **kw):
        """Call API method, return result dict or None."""
        try:
            r = method(**kw)
            if r.get("retCode") == 0:
                return r.get("result")
            log.error("API error %s: %s", kw.get("symbol", ""), r.get("retMsg"))
            return None
        except Exception as e:
            log.error("API call failed: %s", e)
            return None

    def get_contract(self) -> Optional[dict]:
        """Fetch contract info (lot size, min qty, tick, etc.)."""
        if self._contract:
            return self._contract
        r = self._r(
            self.session.get_instruments_info,
            category=self.cfg.CATEGORY,
            symbol=self.cfg.SYMBOL,
        )
        if r and r.get("list"):
            self._contract = r["list"][0]
            return self._contract
        return None

    def get_ticker(self) -> Optional[float]:
        r = self._r(
            self.session.get_tickers,
            category=self.cfg.CATEGORY,
            symbol=self.cfg.SYMBOL,
        )
        if r and r.get("list"):
            return float(r["list"][0]["lastPrice"])
        return None

    def get_klines(self, interval="5", limit=100) -> Optional[pd.DataFrame]:
        r = self._r(
            self.session.get_kline,
            category=self.cfg.CATEGORY,
            symbol=self.cfg.SYMBOL,
            interval=interval,
            limit=limit,
        )
        if r and r.get("list"):
            cols = ["timestamp", "open", "high", "low", "close", "volume", "turnover"]
            df = pd.DataFrame(r["list"], columns=cols)
            for c in ["open", "high", "low", "close", "volume"]:
                df[c] = pd.to_numeric(df[c])
            df["timestamp"] = pd.to_numeric(df["timestamp"])
            return df.sort_values("timestamp").reset_index(drop=True)
        return None

    def get_balance(self) -> Optional[float]:
        r = self._r(self.session.get_wallet_balance, accountType="UNIFIED", coin="USDT")
        if r and r.get("list"):
            for c in r["list"][0].get("coin", []):
                if c["coin"] == "USDT":
                    return float(c["walletBalance"])
        return None

    def get_position(self) -> Optional[dict]:
        r = self._r(
            self.session.get_positions,
            category=self.cfg.CATEGORY,
            symbol=self.cfg.SYMBOL,
        )
        if r and r.get("list"):
            pos = r["list"][0]
            if float(pos.get("size", 0)) > 0:
                return pos
        return None

    # ── order helpers ─────────────────────────────────────────

    def _calc_qty(self, usd_amount: float, price: float) -> float:
        """Round quantity to contract precision."""
        c = self.get_contract()
        if c:
            ls = c.get("lotSizeFilter", {})
            step = float(ls.get("qtyStep", "0.001"))
            raw = usd_amount / price
            return round(raw - (raw % step), 6)
        return round(usd_amount / price, 4)

    def _calc_price(self, price: float) -> float:
        """Round price to tick size."""
        c = self.get_contract()
        if c:
            ps = c.get("priceFilter", {})
            tick = float(ps.get("tickSize", "0.01"))
            return round(price - (price % tick), 6) + tick if price % tick != 0 else round(price, 6)
        return round(price, 2)

    def place_market(self, side: str, usd_amount: float, sl: float, tp: float) -> Optional[str]:
        """Place market order with stop-loss and take-profit. Return order_id or None."""
        price = self.get_ticker()
        if not price:
            return None

        qty = self._calc_qty(usd_amount, price)
        lot_min = float(self.get_contract().get("lotSizeFilter", {}).get("minOrderQty", "0.001"))
        if qty < lot_min:
            log.warning("Qty %f below min %f", qty, lot_min)
            return None

        params = {
            "category": self.cfg.CATEGORY,
            "symbol": self.cfg.SYMBOL,
            "side": side.capitalize(),
            "orderType": "Market",
            "qty": str(qty),
            "timeInForce": "IOC",
            "stopLoss": str(self._calc_price(sl)),
            "takeProfit": str(self._calc_price(tp)),
        }

        r = self._r(self.session.place_order, **params)
        if r:
            oid = r.get("orderId", "?")
            log.info(
                "ORDER %s %s qty=%s sl=%s tp=%s → %s",
                side, self.cfg.SYMBOL, qty, sl, tp, oid,
            )
            return oid
        return None


# ── Strategy ──────────────────────────────────────────────────────
class Strategy:
    """EMA crossover + RSI confirmation. Breakout fallback."""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def evaluate(self, df: pd.DataFrame) -> Optional[dict]:
        if df is None or len(df) < 30:
            return None

        close = df["close"].values
        high = df["high"].values
        low = df["low"].values

        s = pd.Series(close)
        ema9 = EMAIndicator(s, window=9).ema_indicator().values
        ema21 = EMAIndicator(s, window=21).ema_indicator().values
        rsi = RSIIndicator(s, window=14).rsi().values

        p, p1 = close[-1], close[-2]
        e9, e9_1 = ema9[-1], ema9[-2]
        e21, e21_1 = ema21[-1], ema21[-2]
        rsiv = rsi[-1]

        # ATR for SL/TP distance
        atr_arr = np.where(np.isfinite(high[-30:] - low[-30:]), high[-30:] - low[-30:], 0)
        atr = atr_arr[atr_arr > 0].mean() if len(atr_arr[atr_arr > 0]) else p * 0.005
        atr = max(atr, p * 0.002)  # floor

        side = None
        reason = None

        # EMA crossover
        if e9_1 <= e21_1 and e9 > e21 and rsiv > 50:
            side, reason = "Buy", f"EMA9↑EMA21 RSI={rsiv:.0f}"
        elif e9_1 >= e21_1 and e9 < e21 and rsiv < 50:
            side, reason = "Sell", f"EMA9↓EMA21 RSI={rsiv:.0f}"

        # Breakout fallback
        if not side:
            hh, ll = close[-20:].max(), close[-20:].min()
            if p > hh and rsiv > 60:
                side, reason = "Buy", f"Brkout↑ RSI={rsiv:.0f}"
            elif p < ll and rsiv < 40:
                side, reason = "Sell", f"Brkout↓ RSI={rsiv:.0f}"

        if not side:
            return None

        sl = p - atr * 1.5 if side == "Buy" else p + atr * 1.5
        tp = p + atr * 2.5 if side == "Buy" else p - atr * 2.5

        return {"side": side, "price": p, "sl": sl, "tp": tp, "reason": reason}


# ── Risk Manager ──────────────────────────────────────────────────
class RiskManager:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._refresh()

    def _refresh(self):
        self.trades_today = 0
        self.daily_pnl = 0.0
        self.balance = self.cfg.ACCOUNT_BALANCE
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            with open(self.cfg.TRADE_LOG) as f:
                for line in f:
                    try:
                        e = json.loads(line)
                        if e.get("date", "").startswith(today):
                            self.trades_today += 1
                            pnl = e.get("pnl", 0) or 0
                            self.daily_pnl += float(pnl)
                    except Exception:
                        pass
        except FileNotFoundError:
            pass

    def can_trade(self) -> tuple[bool, str]:
        self._refresh()
        if self.trades_today >= self.cfg.MAX_TRADES_PER_DAY:
            return False, f"Max {self.cfg.MAX_TRADES_PER_DAY} trades/day"
        max_loss = self.cfg.ACCOUNT_BALANCE * self.cfg.MAX_DAILY_LOSS_PCT / 100
        if self.daily_pnl <= -max_loss:
            return False, f"Daily loss limit -${max_loss:.0f}"
        return True, "OK"

    def log_trade(self, entry: dict):
        os.makedirs(self.cfg.DATA_DIR, exist_ok=True)
        with open(self.cfg.TRADE_LOG, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")


# ── Notifier ──────────────────────────────────────────────────────
class Notifier:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.tg = None
        if HAS_TELEGRAM and cfg.TELEGRAM_TOKEN and cfg.TELEGRAM_CHAT_ID:
            try:
                self.tg = TelegramBot(token=cfg.TELEGRAM_TOKEN)
                log.info("Telegram notifier ready")
            except Exception as e:
                log.warning("Telegram init failed: %s", e)

    def send(self, msg: str):
        log.info("NOTIFY: %s", msg)
        if self.tg:
            try:
                self.tg.send_message(chat_id=self.cfg.TELEGRAM_CHAT_ID, text=msg)
            except Exception as e:
                log.error("Telegram send error: %s", e)

    def daily_summary(self, bal, trades, dpnl, tpnl):
        self.send(
            f"📊 XAUUSD Daily Summary\n"
            f"Balance: ${bal:.2f}\n"
            f"Trades: {trades}\n"
            f"Day P&L: {dpnl:+.2f} ({dpnl/max(bal,1)*100:+.1f}%)\n"
            f"Total P&L: {tpnl:+.2f}\n"
            f"Updated: {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
        )


# ── Main Loop ─────────────────────────────────────────────────────
def main_loop():
    cfg = Config()
    problems = []

    if not cfg.BYBIT_API_KEY or not cfg.BYBIT_API_SECRET:
        log.fatal("BYBIT_API_KEY and BYBIT_API_SECRET required")
        sys.exit(1)

    log.info("=" * 50)
    log.info("XAUUSD Bot starting")
    log.info("Testnet: %s | Interval: %ss", cfg.USE_TESTNET, cfg.CHECK_INTERVAL)
    log.info("Risk: %d%%/trade | Max %d trades/day | Max %d%% loss/day",
             cfg.RISK_PER_TRADE_PCT, cfg.MAX_TRADES_PER_DAY, cfg.MAX_DAILY_LOSS_PCT)
    log.info("=" * 50)

    client = BybitClient(cfg)
    strat = Strategy(cfg)
    risk = RiskManager(cfg)
    notify = Notifier(cfg)

    # Warm-up: check contract availability
    contract = client.get_contract()
    if not contract:
        notify.send("⚠️ Bot: XAUUSD not found on %s" % ("testnet" if cfg.USE_TESTNET else "mainnet"))
        log.fatal("XAUUSD not available on %s", "testnet" if cfg.USE_TESTNET else "mainnet")
        log.fatal("Set USE_TESTNET=false if on mainnet, or pick a different symbol.")
        # Don't exit — let it keep retrying in case the symbol becomes available
    else:
        log.info("Contract: %s | Status: %s", cfg.SYMBOL, contract.get("status", "?"))
        balance = client.get_balance()
        if balance:
            log.info("Balance: %.2f USDT", balance)

    notify.send("🤖 XAUUSD Trading Bot started")
    last_summary_day = ""

    while True:
        try:
            now = datetime.now(timezone.utc)
            today = now.strftime("%Y-%m-%d")
            hour_min = now.hour * 60 + now.minute

            # ── Daily summary at midnight UTC ──
            if today != last_summary_day and 0 <= hour_min < 3:
                balance = client.get_balance()
                if balance:
                    risk._refresh()
                    total_pnl = balance - cfg.ACCOUNT_BALANCE
                    notify.daily_summary(balance, risk.trades_today, risk.daily_pnl, total_pnl)
                last_summary_day = today

            # ── Already in position? ──
            pos = client.get_position()
            if pos:
                log.info("In position: side=%s size=%s upnl=%s",
                         pos.get("side"), pos.get("size"), pos.get("unrealisedPnl", "?"))
                time.sleep(cfg.CHECK_INTERVAL)
                continue

            # ── Risk gate ──
            ok, reason = risk.can_trade()
            if not ok:
                log.info("Risk gate: %s", reason)
                time.sleep(cfg.CHECK_INTERVAL)
                continue

            # ── Market analysis ──
            df = client.get_klines(interval="5", limit=100)
            if df is None:
                log.warning("No kline data — retrying in 60s")
                time.sleep(60)
                continue

            signal = strat.evaluate(df)
            if not signal:
                log.debug("No signal")
                time.sleep(cfg.CHECK_INTERVAL)
                continue

            # ── Trade ──
            balance = client.get_balance() or cfg.ACCOUNT_BALANCE
            usd_amount = balance * cfg.RISK_PER_TRADE_PCT / 100
            oid = client.place_market(
                signal["side"], usd_amount,
                signal["sl"], signal["tp"],
            )

            if oid:
                risk.log_trade({
                    "ts": now.isoformat(),
                    "date": today,
                    "side": signal["side"],
                    "entry": signal["price"],
                    "sl": signal["sl"],
                    "tp": signal["tp"],
                    "qty_usd": usd_amount,
                    "reason": signal["reason"],
                    "order_id": oid,
                    "action": "entered",
                })
                notify.send(
                    f"🚀 {signal['side']} {cfg.SYMBOL} @ {signal['price']:.2f}\n"
                    f"SL: {signal['sl']:.2f} | TP: {signal['tp']:.2f}\n"
                    f"Size: ${usd_amount:.0f} ({cfg.RISK_PER_TRADE_PCT}%)\n"
                    f"Signal: {signal['reason']}"
                )
                log.info("Trade entered: %s %.2f (order: %s)", signal["side"], signal["price"], oid)
            else:
                log.error("Trade failed to execute")

            time.sleep(cfg.CHECK_INTERVAL)

        except KeyboardInterrupt:
            log.info("Shutdown signal")
            notify.send("🛑 Bot stopped")
            sys.exit(0)
        except Exception as e:
            log.error("Loop error: %s\n%s", e, traceback.format_exc())
            notify.send(f"⚠️ Bot error: {e}")
            time.sleep(60)


if __name__ == "__main__":
    main_loop()
