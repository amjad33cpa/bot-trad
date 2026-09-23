from datetime import datetime, timedelta
from pathlib import Path
import json
from .market import UTC, session_at, stamp
from .strategy import evaluate, market_trend_ok
from .model import ModelGate
from .messages import entry, update
from .providers import Alpaca, Telegram, ProviderError
from .store import Store


class Engine:
    def __init__(self, config):
        self.config = config
        self.market = Alpaca(config)
        self.telegram = Telegram(config)
        self.store = Store(config.db)
        self.gate = ModelGate(config.model_path)
        self.calendar = {}
        self.calendar_loaded = None
        self.assets_loaded = None
        self.assets = set()
        self.status = {"service": "starting", "mode": config.mode, "session": "closed", "last_cycle": None,
                       "last_success": None, "error": None, "active": 0}

    def blacked_out(self, symbol, now):
        # Missing/malformed configured file is an error, not permission to proceed.
        rows = json.loads(Path(self.config.blackout_path).read_text(encoding="utf-8"))
        return any(r["symbol"] in (symbol, "*") and stamp(r["start"]) <= now <= stamp(r["end"]) for r in rows)

    def deliver(self, now):
        ok = True
        for item in self.store.pending(now):
            if item["expires"] and now >= stamp(item["expires"]):
                self.store.cancel_expired_entry(item)
                continue
            if not item["expires"] and not self.store.entry_delivered(item["idea_id"]):
                # Never send an orphan exit after an entry alert expired unsent.
                self.store.delivered(item)
                continue
            try:
                if item["expires"]:
                    row = self.store.db.execute("SELECT data,state FROM ideas WHERE id=?", (item["idea_id"],)).fetchone()
                    data = json.loads(row["data"])
                    q = self.market.quotes([data["symbol"]], data["feed"]).get(data["symbol"])
                    fresh_at = datetime.now(UTC)
                    if row["state"] != "active" or not q or not q.fresh(fresh_at) or not data["entry"] - .01 <= q.ask <= data["entry_max"] or q.bid <= data["stop"] or fresh_at >= stamp(item["expires"]):
                        self.store.cancel_expired_entry(item)
                        continue
                self.telegram.send(item["body"])
                self.store.delivered(item)
            except ProviderError:
                self.store.failed(item, now)
                ok = False
        return ok

    def track(self, quotes_by_feed, now):
        for row in self.store.active():
            data = json.loads(row["data"])
            q = quotes_by_feed.get(data["feed"], {}).get(data["symbol"])
            gap = (now - stamp(row["checked"])).total_seconds()
            expired = now >= stamp(row["expires"])
            kind, price = None, None
            if gap > 90:
                kind = "unknown"
            elif q and q.fresh(now):
                price = q.bid
                if price <= data["stop"]:
                    kind = "stop"
                elif price >= data["target2"]:
                    kind = "target2"
                elif expired:
                    kind = "timeout"
                elif not row["target1"] and price >= data["target1"]:
                    kind = "target1"
                else:
                    self.store.touch(row["id"], now)
            elif expired:
                kind = "unknown"
            if kind:
                risk = data["entry_max"] - data["stop"]
                # Ask entry and observed bid exit; add 5bps per side costs.
                result = None if kind in ("unknown", "target1") else (price - data["entry_max"] - .0005 * (price + data["entry_max"])) / risk
                self.store.event(row, kind, update(data, kind, price, now), now, result)

    def cycle(self, now=None):
        fixed_time = now
        clock = lambda: fixed_time if fixed_time is not None else datetime.now(UTC)
        now = clock()
        self.status.update(last_cycle=now.isoformat(), error=None)
        # Keep delivery independent of market outages, while enforcing entry TTL.
        delivery_ok = self.deliver(now)
        if not self.calendar_loaded or now - self.calendar_loaded > timedelta(hours=6):
            self.calendar = self.market.calendar(now)
            self.calendar_loaded = now
        session = session_at(now, self.calendar, self.config.overnight)
        self.status["session"] = session.name if session else "closed"
        feed = "boats" if session and session.name == "overnight" else self.config.day_feed
        symbols = list(dict.fromkeys((*self.config.symbols, "SPY")))
        if session and session.name == "overnight":
            if not self.assets_loaded or now - self.assets_loaded > timedelta(hours=1):
                self.assets = self.market.overnight_symbols()
                self.assets_loaded = now
            symbols = [s for s in symbols if s in self.assets]
        requests = {}
        if session and (feed != "iex" or session.name == "regular"):
            requests[feed] = set(symbols)
        for row in self.store.active():
            data = json.loads(row["data"])
            requests.setdefault(data["feed"], set()).add(data["symbol"])
        quotes, quote_error = {}, False
        for f, syms in requests.items():
            try:
                quotes[f] = self.market.quotes(sorted(syms), f)
            except ProviderError:
                quote_error = True
        current = clock()
        self.track(quotes, current)
        delivery_ok = self.deliver(current) and delivery_ok
        if quote_error:
            raise ProviderError("QUOTE_UNAVAILABLE")
        if requests and not any(q.fresh(current) for rows in quotes.values() for q in rows.values()):
            raise ProviderError("NO_FRESH_QUOTES")
        if session and symbols and (feed != "iex" or session.name == "regular"):
            bars = self.market.bars(symbols, max(session.start, now - timedelta(minutes=95)), now, feed)
            # Requests take time: re-evaluate freshness at decision time, not request time.
            decision_time = clock()
            for symbol in symbols:
                if symbol not in self.config.symbols or not market_trend_ok(bars.get("SPY", []), session, decision_time):
                    continue
                quote = quotes.get(feed, {}).get(symbol)
                if not quote or self.blacked_out(symbol, decision_time):
                    continue
                c = evaluate(symbol, bars.get(symbol, []), quote, session, decision_time, self.config, feed)
                if not c:
                    continue
                c.ai_score = self.gate.score(c, decision_time, self.config.ai_min_score)
                if self.config.ai_required and c.ai_score is None:
                    continue
                if c.ai_score is not None and c.ai_score < self.config.ai_min_score:
                    continue
                if self.store.can_add(c, self.config):
                    self.store.add(c, entry(c))
            delivery_ok = self.deliver(decision_time) and delivery_ok
        if not delivery_ok:
            raise ProviderError("TELEGRAM_UNAVAILABLE")
        self.status.update(service="running", last_success=datetime.now(UTC).isoformat(), active=len(self.store.active()))


def check_live_model(config, gate):
    if config.mode == "live" and not any(m.get("accepted") and
            stamp(m["trained_at"]) <= datetime.now(UTC) <= stamp(m["trained_at"]) + timedelta(days=30)
            for m in gate.models.values()):
        raise ValueError("Live alerts need an accepted, recent model; collect paper observations first")
