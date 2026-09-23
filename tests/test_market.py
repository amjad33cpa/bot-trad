import unittest
from datetime import datetime, timedelta
from dataclasses import replace
from sentinel.market import session_at, stamp, Quote, Bar, UTC
from sentinel.demo import fixture
from sentinel.strategy import evaluate, market_trend_ok
from sentinel.config import Config


class Sessions(unittest.TestCase):
    def setUp(self):
        self.cal = {"2026-09-21": {"open": "09:30", "close": "16:00"},
                    "2026-11-27": {"open": "09:30", "close": "13:00"}}

    def test_sunday_overnight_and_trade_date(self):
        s = session_at(stamp("2026-09-21T01:00:00Z"), self.cal, True)
        self.assertEqual((s.name, s.trade_date), ("overnight", "2026-09-21"))

    def test_closed_weekend_holiday_and_disabled_overnight(self):
        for t in ("2026-09-19T16:00:00Z", "2026-11-26T16:00:00Z", "2026-09-21T01:00:00Z"):
            self.assertIsNone(session_at(stamp(t), self.cal))

    def test_half_day(self):
        s = session_at(stamp("2026-11-27T18:00:00Z"), self.cal)
        self.assertEqual(s.name, "after")
        self.assertIsNone(session_at(stamp("2026-11-27T22:00:00Z"), self.cal))

    def test_dst_session_boundaries(self):
        self.assertEqual(session_at(stamp("2026-09-21T13:30:00Z"), self.cal).name, "regular")
        self.assertEqual(session_at(stamp("2026-11-27T14:30:00Z"), self.cal).name, "regular")
        self.assertEqual(session_at(stamp("2026-09-21T13:29:59Z"), self.cal).name, "pre")


class Strategy(unittest.TestCase):
    def setUp(self):
        self.now, self.session, self.bars, self.quote = fixture()

    def evaluate(self, **kwargs):
        args = dict(symbol="DEMO", bars=self.bars, quote=self.quote, session=self.session,
                    now=self.now, config=Config(), feed="iex")
        args.update(kwargs)
        return evaluate(**args)

    def test_candidate_geometry_and_risk(self):
        c = self.evaluate()
        self.assertIsNotNone(c)
        self.assertLess(c.stop, c.entry)
        self.assertLess(c.entry_max, c.target1)
        self.assertGreater((c.target2 - c.entry_max) / (c.entry_max - c.stop), 2)
        self.assertLessEqual(c.risk_usd, 25)
        self.assertLessEqual(c.shares * c.entry_max, 2000)

    def test_stale_future_crossed_nan_and_wide_quotes(self):
        for q in (replace(self.quote, t=self.now-timedelta(seconds=21)),
                  replace(self.quote, t=self.now+timedelta(seconds=1)),
                  replace(self.quote, bid=200), replace(self.quote, ask=float('nan')),
                  replace(self.quote, bid=self.quote.ask-1), replace(self.quote, ask_size=0)):
            self.assertIsNone(self.evaluate(quote=q))

    def test_incomplete_bar_cannot_trigger(self):
        bars = self.bars[:-1] + [replace(self.bars[-1], t=self.now.replace(second=0))]
        self.assertIsNone(self.evaluate(bars=bars))

    def test_missing_minute_rejected(self):
        self.assertIsNone(self.evaluate(bars=self.bars[:40]+self.bars[41:]))

    def test_volume_and_chasing_filters(self):
        self.assertIsNone(self.evaluate(bars=[replace(b, v=1) for b in self.bars]))
        self.assertIsNone(self.evaluate(quote=replace(self.quote, bid=102, ask=102.01)))

    def test_no_entries_near_session_close(self):
        self.assertIsNone(self.evaluate(session=replace(self.session, end=self.now+timedelta(minutes=5))))

    def test_benchmark_downtrend(self):
        self.assertTrue(market_trend_ok(self.bars, self.session, self.now))
        self.assertFalse(market_trend_ok([replace(b, c=200-b.c) for b in self.bars], self.session, self.now))

    def test_bar_parser_rejects_impossible_prices(self):
        with self.assertRaises(ValueError):
            Bar.parse({"t": self.now.isoformat(), "o": 100, "h": 99, "l": 98, "c": 100, "v": 5})

    def test_no_naive_timestamps(self):
        with self.assertRaises(ValueError):
            stamp("2026-09-23T12:00:00")


if __name__ == "__main__":
    unittest.main()
