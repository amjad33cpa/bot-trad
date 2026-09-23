import json
import math
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import replace
from sentinel.config import Config
from sentinel.market import UTC
from sentinel.model import train, fit, predict, ModelGate
from sentinel.strategy import FEATURES, VERSION, evaluate
from sentinel.demo import fixture


class ModelTests(unittest.TestCase):
    def rows(self, n=400):
        start = datetime(2025, 1, 1, tzinfo=UTC)
        return [dict(created=(start+timedelta(hours=i*3)).isoformat(),
                     closed=(start+timedelta(hours=i*3+1)).isoformat(),
                     features=[float(i % 2)]*len(FEATURES), result_r=2 if i%2 else -1.2,
                     outcome="target2" if i%2 else "stop", feed="iex", session="regular", strategy=VERSION,
                     ai_score=None) for i in range(n)]

    def test_insufficient_data_never_accepted(self):
        model, report = train(self.rows(20))
        self.assertEqual(model["models"], {})
        self.assertFalse(report["iex:regular"]["accepted"])

    def test_chronological_holdout_purges_unresolved_outcomes(self):
        rows = self.rows()
        cutoff = rows[300]["created"]
        rows[10]["closed"] = cutoff
        model, report = train(rows)
        stats = report["iex:regular"]
        self.assertEqual(stats["train_n"], 299)
        self.assertEqual(stats["test_n"], 100)
        self.assertTrue(stats["accepted"])

    def test_no_models_cross_feed_and_session(self):
        blob, _ = train(self.rows())
        now, session, bars, quote = fixture()
        c = evaluate("DEMO", bars, quote, session, now, Config(), "boats")
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/"model.json"
            p.write_text(json.dumps(blob))
            gate = ModelGate(p)
            self.assertIsNone(gate.score(c, datetime.now(UTC)))
            self.assertIsNone(gate.score(replace(c, feed="iex"), datetime.now(UTC)+timedelta(days=31)))
            self.assertIsNone(gate.score(replace(c, feed="iex"), datetime.now(UTC), .9))

    def test_json_schema_dimensions(self):
        blob, _ = train(self.rows())
        blob["models"]["iex:regular"]["weights"] = [1]
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/"model.json"
            p.write_text(json.dumps(blob))
            with self.assertRaises(ValueError):
                ModelGate(p)

    def test_feedback_filtered_observations_excluded(self):
        rows = self.rows()
        for row in rows:
            row["ai_score"] = .8
        self.assertEqual(train(rows)[0]["models"], {})

    def test_live_requires_paid_feed_and_model(self):
        config = Config(mode="live", key="x", secret="x", token="x", chat="1")
        with self.assertRaises(ValueError):
            config.validate()

    def test_nonfinite_config_rejected(self):
        with self.assertRaises(ValueError):
            Config(key="x", secret="x", token="x", chat="1", risk=float('nan')).validate()
