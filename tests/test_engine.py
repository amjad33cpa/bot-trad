import json
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch
from sentinel.config import Config
from sentinel.demo import fixture
from sentinel.engine import Engine
from sentinel.market import Quote
from sentinel.providers import Alpaca, ProviderError
from sentinel.strategy import evaluate
from sentinel.messages import entry
from sentinel.store import Store


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now, self.session, self.bars, self.q = fixture()
        self.cfg = Config(db=str(Path(self.tmp.name)/"db.sqlite"), model_path=str(Path(self.tmp.name)/"none.json"), symbols=("DEMO",))
        self.engine = Engine(self.cfg)
        self.engine.market = Mock()
        self.engine.telegram = Mock()
        self.c = evaluate("DEMO", self.bars, self.q, self.session, self.now, self.cfg, "iex")
        self.engine.store.add(self.c, entry(self.c))

    def tearDown(self):
        self.engine.store.db.close()
        self.tmp.cleanup()

    def test_duplicate_and_cooldown_persist(self):
        self.assertFalse(self.engine.store.add(self.c, "duplicate"))
        self.assertFalse(self.engine.store.can_add(self.c, self.cfg))
        other = Store(self.cfg.db)
        self.assertEqual(len(other.active()), 1)
        other.db.close()

    def test_target1_then_target2(self):
        for offset, price in ((20, self.c.target1), (40, self.c.target2)):
            t = self.now + timedelta(seconds=offset)
            q = Quote(t, price, price+.01, 10, 10)
            self.engine.track({"iex": {"DEMO": q}}, t)
        self.assertEqual(len(self.engine.store.active()), 0)
        rows = self.engine.store.export()
        self.assertEqual(rows[0]["outcome"], "target2")
        self.assertLess(rows[0]["result_r"], 2.5)  # costs and conservative entry

    def test_gap_down_uses_observed_bid_not_stop(self):
        t = self.now + timedelta(seconds=20)
        q = Quote(t, self.c.stop-1, self.c.stop-.99, 10, 10)
        self.engine.track({"iex": {"DEMO": q}}, t)
        self.assertLess(self.engine.store.export()[0]["result_r"], -1)

    def test_missing_data_excluded_from_training(self):
        self.engine.track({}, self.now + timedelta(seconds=91))
        self.assertEqual(self.engine.store.export(), [])
        row = self.engine.store.db.execute("SELECT state FROM ideas").fetchone()
        self.assertEqual(row[0], "unknown")

    def test_expired_unsent_entry_not_sent(self):
        self.engine.deliver(self.now + timedelta(seconds=61))
        self.engine.telegram.send.assert_not_called()
        self.assertEqual(len(self.engine.store.active()), 0)

    def test_price_revalidated_before_telegram(self):
        self.engine.market.quotes.return_value = {"DEMO": replace(self.q, ask=200, bid=199)}
        with patch("sentinel.engine.datetime") as dt:
            dt.now.return_value = self.now
            self.engine.deliver(self.now)
        self.engine.telegram.send.assert_not_called()
        self.assertEqual(len(self.engine.store.active()), 0)

    def test_delivery_retry_does_not_duplicate_success(self):
        self.engine.market.quotes.return_value = {"DEMO": self.q}
        self.engine.telegram.send.side_effect = [ProviderError("timeout"), 123]
        with patch("sentinel.engine.datetime") as dt:
            dt.now.return_value = self.now
            self.assertFalse(self.engine.deliver(self.now))
            self.assertTrue(self.engine.deliver(self.now + timedelta(seconds=3)))
            self.assertTrue(self.engine.deliver(self.now + timedelta(seconds=4)))
        self.assertEqual(self.engine.telegram.send.call_count, 2)

    def test_loss_limit_blocks_new_symbol(self):
        row = self.engine.store.active()[0]
        self.engine.store.event(row, "stop", "loss", self.now, -3)
        self.assertFalse(self.engine.store.can_add(replace(self.c, symbol="OTHER"), self.cfg))

    def test_orphan_update_not_sent(self):
        self.engine.track({}, self.now + timedelta(seconds=91))
        self.engine.deliver(self.now + timedelta(seconds=91))
        self.engine.telegram.send.assert_not_called()

    def test_full_cycle_records_and_sends(self):
        self.engine.store.db.execute("DELETE FROM ideas")
        self.engine.store.db.execute("DELETE FROM outbox")
        self.engine.store.db.commit()
        self.engine.market.calendar.return_value = {"2026-09-23": {"open":"09:30", "close":"16:00"}}
        self.engine.market.bars.return_value = {"DEMO": self.bars, "SPY": self.bars}
        self.engine.market.quotes.return_value = {"DEMO": self.q, "SPY": self.q}
        with patch("sentinel.engine.datetime") as dt:
            dt.now.return_value = self.now
            self.engine.cycle(self.now)
        self.assertEqual(self.engine.telegram.send.call_count, 1)
        self.assertEqual(self.engine.status["service"], "running")

    def test_free_feed_skips_premarket(self):
        self.engine.store.db.execute("DELETE FROM ideas")
        self.engine.store.db.execute("DELETE FROM outbox")
        self.engine.store.db.commit()
        self.engine.market.calendar.return_value = {"2026-09-23": {"open":"09:30", "close":"16:00"}}
        self.engine.cycle(self.now.replace(hour=12))
        self.engine.market.bars.assert_not_called()


class ProviderTests(unittest.TestCase):
    def test_overnight_eligibility_and_halts(self):
        p = Alpaca(Config())
        p.get = Mock(return_value=[
            {"symbol":"AAPL", "tradable":True, "attributes":["overnight_tradable"]},
            {"symbol":"HALT", "tradable":True, "attributes":["overnight_tradable","overnight_halted"]},
            {"symbol":"NO", "tradable":True, "attributes":[]},
        ])
        self.assertEqual(p.overnight_symbols(), {"AAPL"})

    def test_bars_pagination(self):
        now, _, bars, _ = fixture()
        b = bars[0]
        row = dict(t=b.t.isoformat(), o=b.o, h=b.h, l=b.l, c=b.c, v=b.v, vw=b.vw)
        p = Alpaca(Config())
        p.get = Mock(side_effect=[{"bars":{"AAPL":[row]}, "next_page_token":"two"}, {"bars":{"MSFT":[row]}}])
        result = p.bars(["AAPL", "MSFT"], now-timedelta(hours=1), now, "iex")
        self.assertEqual(set(result), {"AAPL", "MSFT"})
        self.assertEqual(p.get.call_count, 2)

    def test_no_credential_in_network_exception(self):
        from urllib.error import URLError
        from sentinel.providers import request_json
        with patch("sentinel.providers.urlopen", side_effect=URLError("SECRET_TOKEN")):
            with self.assertRaises(ProviderError) as cm:
                request_json("https://example.test/SECRET_TOKEN", retries=0)
        self.assertNotIn("SECRET_TOKEN", str(cm.exception))
