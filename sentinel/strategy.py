from dataclasses import dataclass, asdict
from datetime import timedelta
from statistics import mean
import math

FEATURES = ["trend_atr", "breakout_atr", "relative_volume", "vwap_atr", "spread_bps", "atr_fraction"]
VERSION = "breakout-v1"


def ema(values, period):
    value = values[0]
    for x in values[1:]:
        value += 2 / (period + 1) * (x - value)
    return value


def market_trend_ok(bars, session, now):
    bars = sorted({b.t: b for b in bars if session.start <= b.t and b.t + timedelta(minutes=1) <= now}.values(), key=lambda b: b.t)
    if len(bars) < 21 or (now - bars[-1].t).total_seconds() > 150:
        return False
    closes = [b.c for b in bars[-55:]]
    return closes[-1] >= ema(closes, 21) and ema(closes, 9) >= ema(closes, 21)


@dataclass
class Candidate:
    symbol: str
    session: str
    session_key: str
    trade_date: str
    created: str
    bar_time: str
    expires: str
    entry: float
    entry_max: float
    stop: float
    target1: float
    target2: float
    shares: int
    risk_usd: float
    features: list
    feed: str
    mode: str
    ai_score: float | None = None
    strategy: str = VERSION

    def dict(self):
        return asdict(self)


def evaluate(symbol, bars, quote, session, now, config, feed):
    # Use completed, distinct bars from this session only; never mix SIP/BOATS.
    bars = sorted({b.t: b for b in bars if session.start <= b.t and b.t + timedelta(minutes=1) <= now}.values(), key=lambda b: b.t)
    if len(bars) < 55 or not quote.fresh(now) or session.end - now < timedelta(minutes=15):
        return None
    bars = bars[-90:]
    if (now - bars[-1].t).total_seconds() > 150:
        return None
    if any((b.t - a.t).total_seconds() != 60 for a, b in zip(bars[-55:], bars[-54:])):
        return None
    closes = [b.c for b in bars]
    last = bars[-1]
    atr = mean(max(b.h - b.l, abs(b.h - a.c), abs(b.l - a.c)) for a, b in zip(bars[-15:-1], bars[-14:]))
    if atr <= 0 or not 5 <= quote.ask <= 3000 or not .0005 <= atr / quote.ask <= .025:
        return None
    spread_bps = (quote.ask - quote.bid) / quote.ask * 10000
    if spread_bps > (12 if session.name == "regular" else 20):
        return None
    if mean(b.c * b.v for b in bars[-20:]) < (100000 if session.name == "regular" else 150000):
        return None
    volume = mean(b.v for b in bars[-21:-1])
    rvol = last.v / volume if volume else 0
    vwap = sum(b.vw * b.v for b in bars) / max(1, sum(b.v for b in bars))
    fast, slow = ema(closes, 9), ema(closes, 21)
    resistance = max(b.h for b in bars[-21:-1])
    # Confirm both short and slower trend, volume expansion and a closed-bar breakout.
    if not (last.c > resistance and fast > slow > ema(closes, 50) and last.c > vwap and rvol >= 1.5):
        return None
    if not 0 <= quote.ask - last.c <= .5 * atr or last.c - vwap > 3 * atr:
        return None
    entry = round(quote.ask, 2)
    stop = round(min(entry - 1.5 * atr, min(b.l for b in bars[-5:])), 2)
    risk = entry - stop
    if risk <= 0 or not .002 <= risk / entry <= .03:
        return None
    entry_max = round(entry + .1 * risk, 2)
    shares = math.floor(min(config.equity * config.risk / (entry_max - stop), config.equity * .2 / entry_max))
    if shares < 1:
        return None
    return Candidate(symbol, session.name, session.key, session.trade_date, now.isoformat(), last.t.isoformat(),
                     min(now + timedelta(minutes=90), session.end).isoformat(), entry, entry_max, stop,
                     round(entry + risk, 2), round(entry + 2.5 * risk, 2), shares,
                     round(shares * (entry_max - stop), 2),
                     [(fast - slow) / atr, (last.c - resistance) / atr, rvol, (last.c - vwap) / atr, spread_bps, atr / entry],
                     feed, config.mode)
