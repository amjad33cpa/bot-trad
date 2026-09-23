from dataclasses import dataclass
import os
import re
import math
from pathlib import Path


@dataclass(frozen=True)
class Config:
    mode: str = "paper"
    key: str = ""
    secret: str = ""
    trading_url: str = "https://paper-api.alpaca.markets"
    day_feed: str = "iex"
    overnight: bool = False
    token: str = ""
    chat: str = ""
    symbols: tuple = ("AAPL", "MSFT", "NVDA", "SPY", "QQQ")
    poll: int = 30
    db: str = "data/sentinel.sqlite3"
    model_path: str = "data/model.json"
    ai_required: bool = False
    ai_min_score: float = .60
    equity: float = 10000
    risk: float = .0025
    max_active: int = 3
    max_daily: int = 5
    max_loss_r: float = 2
    blackout_path: str = "config/blackouts.json"
    port: int = 8080

    @classmethod
    def env(cls):
        def boolean(name, default="false"):
            val = os.getenv(name, default).lower()
            if val not in ("true", "false"):
                raise ValueError(f"{name} must be true or false")
            return val == "true"
        c = cls(
            mode=os.getenv("MODE", "paper"), key=os.getenv("ALPACA_KEY", ""),
            secret=os.getenv("ALPACA_SECRET", ""),
            trading_url=os.getenv("ALPACA_TRADING_URL", cls.trading_url),
            day_feed=os.getenv("DAY_FEED", "iex"), overnight=boolean("ENABLE_OVERNIGHT"),
            token=os.getenv("TELEGRAM_BOT_TOKEN", ""), chat=os.getenv("TELEGRAM_CHAT_ID", ""),
            symbols=tuple(dict.fromkeys(os.getenv("WATCHLIST", "AAPL,MSFT,NVDA,AMZN,META,GOOGL,TSLA,AMD,AVGO,SPY,QQQ").upper().split(","))),
            poll=int(os.getenv("POLL_SECONDS", "30")), db=os.getenv("DATABASE_PATH", cls.db),
            model_path=os.getenv("MODEL_PATH", cls.model_path), ai_required=boolean("AI_REQUIRED"),
            ai_min_score=float(os.getenv("AI_MIN_SCORE", ".60")),
            equity=float(os.getenv("PAPER_EQUITY", "10000")), risk=float(os.getenv("RISK_FRACTION", ".0025")),
            max_active=int(os.getenv("MAX_ACTIVE", "3")), max_daily=int(os.getenv("MAX_ALERTS_PER_DAY", "5")),
            max_loss_r=float(os.getenv("MAX_DAILY_LOSS_R", "2")),
            blackout_path=os.getenv("BLACKOUT_PATH", cls.blackout_path), port=int(os.getenv("PORT", "8080")),
        )
        c.validate()
        return c

    def validate(self):
        if self.mode not in ("paper", "live") or self.day_feed not in ("iex", "sip"):
            raise ValueError("MODE must be paper/live and DAY_FEED iex/sip")
        if not all((self.key, self.secret, self.token, self.chat)):
            raise ValueError("Set Alpaca and Telegram credentials in environment variables")
        if self.trading_url not in ("https://paper-api.alpaca.markets", "https://api.alpaca.markets"):
            raise ValueError("Unrecognized Alpaca host")
        if not 15 <= self.poll <= 60 or not 0 < self.risk <= .01 or not math.isfinite(self.equity) or not self.equity > 0:
            raise ValueError("Invalid polling or paper risk limits")
        if not 1 <= self.max_active <= 10 or not 1 <= self.max_daily <= 50 or not 0 < self.max_loss_r <= 10:
            raise ValueError("Invalid alert limits")
        if not .5 <= self.ai_min_score < 1 or not 1 <= self.port <= 65535:
            raise ValueError("Invalid model threshold or port")
        if not 1 <= len(self.symbols) <= 50 or any(not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", s) for s in self.symbols):
            raise ValueError("WATCHLIST needs 1-50 valid symbols without spaces")
        if self.mode == "live" and (self.day_feed != "sip" or not self.ai_required):
            raise ValueError("Live alerts require SIP and AI_REQUIRED=true")
        if os.getenv("RAILWAY_ENVIRONMENT_ID"):
            mount = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
            if not mount or not Path(self.db).resolve().is_relative_to(Path(mount).resolve()):
                raise ValueError("Railway paper/live requires DATABASE_PATH inside a mounted persistent volume")
