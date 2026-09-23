"""Small auditable logistic classifier. JSON weights only; never load pickle."""
from datetime import datetime, timedelta
from pathlib import Path
import json
import math
from .market import stamp, UTC
from .strategy import FEATURES, VERSION


def sigmoid(z):
    return 1 / (1 + math.exp(-max(-35, min(35, z))))


def predict(model, features):
    if len(features) != len(FEATURES) or not all(math.isfinite(x) for x in features):
        raise ValueError("Invalid model features")
    xs = [(x - m) / s for x, m, s in zip(features, model["means"], model["scales"])]
    return sigmoid(model["bias"] + sum(x * w for x, w in zip(xs, model["weights"])))


class ModelGate:
    def __init__(self, path):
        self.models = {}
        if Path(path).exists():
            blob = json.loads(Path(path).read_text(encoding="utf-8"))
            if blob["features"] != FEATURES or blob["strategy"] != VERSION:
                raise ValueError("Incompatible model artifact")
            self.models = blob["models"]
            for m in self.models.values():
                if any(len(m[k]) != len(FEATURES) for k in ("weights", "means", "scales")):
                    raise ValueError("Invalid model dimensions")
                if not all(math.isfinite(x) for k in ("weights", "means", "scales") for x in m[k]) or not math.isfinite(m["bias"]) or any(x <= 0 for x in m["scales"]):
                    raise ValueError("Non-finite model")

    def score(self, candidate, now, threshold=.6):
        model = self.models.get(f"{candidate.feed}:{candidate.session}")
        if not model or not model.get("accepted") or abs(model["threshold"] - threshold) > 1e-9:
            return None
        trained = stamp(model["trained_at"])
        if not trained <= now <= trained + timedelta(days=30):
            return None
        return predict(model, candidate.features)


def fit(rows):
    n, width = len(rows), len(FEATURES)
    means = [sum(r["features"][j] for r in rows) / n for j in range(width)]
    scales = [max(1e-6, (sum((r["features"][j] - means[j]) ** 2 for r in rows) / n) ** .5) for j in range(width)]
    xs = [[(x - m) / s for x, m, s in zip(r["features"], means, scales)] for r in rows]
    ys = [int(r["outcome"] == "target2") for r in rows]
    weights, bias = [0.] * width, 0.
    for _ in range(800):
        errors = [sigmoid(bias + sum(w * x for w, x in zip(weights, row))) - y for row, y in zip(xs, ys)]
        bias -= .05 * sum(errors) / n
        weights = [w - .05 * (sum(e * row[j] for e, row in zip(errors, xs)) / n + .01 * w) for j, w in enumerate(weights)]
    return {"weights": weights, "bias": bias, "means": means, "scales": scales}


def train(rows, threshold=.6):
    groups = {}
    for row in rows:
        if row.get("strategy") != VERSION or row.get("outcome") not in ("target2", "stop", "timeout"):
            continue
        if len(row["features"]) != len(FEATURES) or not all(math.isfinite(x) for x in row["features"]) or not math.isfinite(row["result_r"]):
            raise ValueError("Invalid training row")
        if stamp(row["closed"]) < stamp(row["created"]):
            raise ValueError("Outcome precedes entry")
        if row.get("ai_score") is not None:
            # Train only on unfiltered collection; avoid feedback from the model gate.
            continue
        groups.setdefault(f"{row['feed']}:{row['session']}", []).append(row)
    models, report = {}, {}
    for group, data in groups.items():
        data.sort(key=lambda r: stamp(r["created"]))
        if len(data) < 300:
            report[group] = {"accepted": False, "reason": "Need at least 300 unfiltered resolved samples", "n": len(data)}
            continue
        cutoff = stamp(data[int(len(data) * .75)]["created"])
        train_rows = [r for r in data if stamp(r["closed"]) < cutoff - timedelta(minutes=90)]
        test_rows = [r for r in data if stamp(r["created"]) >= cutoff]
        if len(train_rows) < 200 or len(test_rows) < 50 or len({r["outcome"] == "target2" for r in train_rows}) < 2:
            report[group] = {"accepted": False, "reason": "Insufficient purged training/holdout classes"}
            continue
        model = fit(train_rows)
        chosen = [r for r in test_rows if predict(model, r["features"]) >= threshold]
        precision = sum(r["outcome"] == "target2" for r in chosen) / max(1, len(chosen))
        avg_r = sum(r["result_r"] for r in chosen) / max(1, len(chosen))
        # A research acceptance screen, never proof of future profitability.
        accepted = len(chosen) >= 30 and precision >= .55 and avg_r > .1
        stats = {"accepted": accepted, "train_n": len(train_rows), "test_n": len(test_rows), "selected_n": len(chosen),
                 "observed_precision": precision, "mean_net_r": avg_r, "threshold": threshold, "cutoff": cutoff.isoformat()}
        model.update(stats, trained_at=datetime.now(UTC).isoformat())
        models[group], report[group] = model, stats
    return {"features": FEATURES, "strategy": VERSION, "models": models}, report
