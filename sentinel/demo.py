from datetime import datetime, timedelta
from dataclasses import replace
from .market import Bar, Quote, Session, UTC
from .strategy import evaluate
from .config import Config
from .messages import entry


def fixture():
    now = datetime(2026, 9, 23, 15, 30, 10, tzinfo=UTC)
    start = now.replace(hour=13, minute=30, second=0)
    session = Session("regular", start, now.replace(hour=20, minute=0, second=0), "2026-09-23")
    bars = []
    for i in range(70):
        c = 100 + .005 * i
        if i == 69:
            c += .11
        bars.append(Bar(now.replace(second=0) - timedelta(minutes=70-i), c-.015, c+.045, c-.075, c, 7000 if i == 69 else 2500, c))
    quote = Quote(now, bars[-1].c + .005, bars[-1].c + .015, 200, 200)
    return now, session, bars, quote


def demo():
    now, session, bars, quote = fixture()
    candidate = evaluate("DEMO", bars, quote, session, now, Config(), "iex")
    if candidate is None:
        raise RuntimeError("Demo fixture failed")
    print("بيانات اصطناعية لا تخص سهمًا فعليًا — لا توجد اتصالات خارجية\n")
    print(entry(candidate))
    return candidate
