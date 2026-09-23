"""Read-only market API calls and Telegram delivery; no broker order endpoints."""
import json
import time
import re
from datetime import timedelta
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
from .market import Bar, Quote


class ProviderError(RuntimeError):
    def __init__(self, code):
        self.safe_code = code if re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", str(code)) else "PROVIDER_ERROR"
        super().__init__(self.safe_code)


def request_json(url, headers=None, payload=None, retries=2):
    body = None if payload is None else json.dumps(payload).encode()
    headers = dict(headers or {})
    if body is not None:
        headers["Content-Type"] = "application/json"
    for attempt in range(retries + 1):
        try:
            with urlopen(Request(url, data=body, headers=headers), timeout=12) as response:
                return json.load(response)
        except HTTPError as exc:
            # Never log exception text: Telegram URLs contain the secret token.
            if exc.code not in (429, 500, 502, 503, 504) or attempt == retries:
                raise ProviderError(f"HTTP_{exc.code}") from None
            try:
                delay = min(15, max(1, int(exc.headers.get("Retry-After", 2 ** attempt))))
            except (ValueError, TypeError):
                delay = 2 ** attempt
        except (URLError, TimeoutError, OSError, ValueError):
            if attempt == retries:
                raise ProviderError("NETWORK_OR_JSON_ERROR") from None
            delay = 2 ** attempt
        time.sleep(delay)


class Alpaca:
    def __init__(self, config):
        self.config = config
        self.headers = {"APCA-API-KEY-ID": config.key, "APCA-API-SECRET-KEY": config.secret}

    def get(self, path, params=None, trading=False):
        base = self.config.trading_url if trading else "https://data.alpaca.markets"
        return request_json(base + path + ("?" + urlencode(params) if params else ""), self.headers)

    def calendar(self, now):
        rows = self.get("/v2/calendar", {"start": (now - timedelta(days=1)).date().isoformat(),
                                       "end": (now + timedelta(days=8)).date().isoformat()}, trading=True)
        return {row["date"]: row for row in rows}

    def overnight_symbols(self):
        rows = self.get("/v2/assets", {"status": "active", "asset_class": "us_equity"}, trading=True)
        return {r["symbol"] for r in rows if r.get("tradable")
                and "overnight_tradable" in r.get("attributes", [])
                and "overnight_halted" not in r.get("attributes", [])}

    def bars(self, symbols, start, end, feed):
        if not symbols:
            return {}
        params = {"symbols": ",".join(symbols), "timeframe": "1Min", "start": start.isoformat(),
                  "end": end.isoformat(), "feed": feed, "adjustment": "raw", "limit": 10000, "sort": "asc"}
        result, seen = {}, set()
        while True:
            page = self.get("/v2/stocks/bars", params)
            for symbol, rows in (page.get("bars") or {}).items():
                result.setdefault(symbol, []).extend(Bar.parse(r) for r in rows)
            token = page.get("next_page_token")
            if not token:
                return result
            if token in seen or len(seen) >= 20:
                raise ProviderError("PAGINATION_LOOP")
            seen.add(token)
            params["page_token"] = token

    def quotes(self, symbols, feed):
        if not symbols:
            return {}
        data = self.get("/v2/stocks/quotes/latest", {"symbols": ",".join(symbols), "feed": feed})
        return {s: Quote.parse(q) for s, q in (data.get("quotes") or {}).items()}


class Telegram:
    def __init__(self, config):
        self.url = f"https://api.telegram.org/bot{config.token}/sendMessage"
        self.chat = config.chat

    def send(self, text):
        data = request_json(self.url, payload={"chat_id": self.chat, "text": text, "disable_web_page_preview": True}, retries=0)
        if not data.get("ok"):
            raise ProviderError("TELEGRAM_REJECTED")
        return data["result"]["message_id"]
