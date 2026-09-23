from dataclasses import dataclass
from datetime import datetime, timedelta, time, timezone
from zoneinfo import ZoneInfo
import math

UTC = timezone.utc
NY = ZoneInfo("America/New_York")
RIYADH = ZoneInfo("Asia/Riyadh")


def stamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timezone required")
    return result.astimezone(UTC)


@dataclass(frozen=True)
class Session:
    name: str
    start: datetime
    end: datetime
    trade_date: str

    @property
    def key(self):
        return f"{self.trade_date}:{self.name}"


def session_at(now, calendar, overnight=False):
    local = now.astimezone(NY)
    day = local.date()
    t = local.time().replace(tzinfo=None)
    def at(d, clock):
        return datetime.combine(d, clock, NY).astimezone(UTC)
    if t >= time(20) or t < time(4):
        target = day + timedelta(days=1) if t >= time(20) else day
        if not overnight or target.isoformat() not in calendar:
            return None
        return Session("overnight", at(target - timedelta(days=1), time(20)), at(target, time(4)), target.isoformat())
    item = calendar.get(day.isoformat())
    if not item:
        return None
    opening = time.fromisoformat(item["open"])
    closing = time.fromisoformat(item["close"])
    # Conservative half-day policy: stop after-hours four hours after early close.
    after_end = min(at(day, time(20)), at(day, closing) + timedelta(hours=4))
    if time(4) <= t < opening:
        return Session("pre", at(day, time(4)), at(day, opening), day.isoformat())
    if opening <= t < closing:
        return Session("regular", at(day, opening), at(day, closing), day.isoformat())
    if now < after_end and t >= closing:
        return Session("after", at(day, closing), after_end, day.isoformat())
    return None


@dataclass(frozen=True)
class Bar:
    t: datetime
    o: float
    h: float
    l: float
    c: float
    v: float
    vw: float

    @classmethod
    def parse(cls, x):
        b = cls(stamp(x["t"]), *[float(x[k]) for k in ("o", "h", "l", "c", "v")], float(x.get("vw") or x["c"]))
        if not all(math.isfinite(v) for v in (b.o, b.h, b.l, b.c, b.v, b.vw)) or not (0 < b.l <= min(b.o, b.c) <= max(b.o, b.c) <= b.h) or b.v < 0 or b.vw <= 0:
            raise ValueError("Invalid OHLCV")
        return b


@dataclass(frozen=True)
class Quote:
    t: datetime
    bid: float
    ask: float
    bid_size: float
    ask_size: float

    @classmethod
    def parse(cls, x):
        return cls(stamp(x["t"]), *[float(x[k]) for k in ("bp", "ap", "bs", "as")])

    def fresh(self, now):
        return (all(math.isfinite(v) for v in (self.bid, self.ask, self.bid_size, self.ask_size))
                and 0 < self.bid <= self.ask and self.bid_size > 0 and self.ask_size > 0
                and 0 <= (now - self.t).total_seconds() <= 20)
