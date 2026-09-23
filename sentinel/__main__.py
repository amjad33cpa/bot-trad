import argparse
import json
import logging
import os
import sys
from pathlib import Path
from .config import Config
from .demo import demo
from .model import train
from .service import run, setup_server
from .store import Store


def main():
    parser = argparse.ArgumentParser(description="Autonomous US equity research alerts")
    parser.add_argument("command", choices=("run", "demo", "export", "train"), nargs="?", default="run")
    parser.add_argument("--database", default="data/sentinel.sqlite3")
    parser.add_argument("--input", default="data/observations.json")
    parser.add_argument("--output")
    parser.add_argument("--threshold", type=float, default=.6)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(asctime)s %(levelname)s %(message)s")
    if args.command == "demo":
        demo()
    elif args.command == "run":
        try:
            if os.getenv("MODE") == "setup":
                setup_server(int(os.getenv("PORT", "8080")))
            else:
                run(Config.env())
        except (ValueError, OSError) as exc:
            # Config failures contain no secret values.
            logging.error("startup_failed class=%s; verify configuration and model file", type(exc).__name__)
            raise SystemExit(1) from None
    elif args.command == "export":
        if not Path(args.database).exists():
            parser.error("Database does not exist")
        store = Store(args.database)
        output = Path(args.output or "data/observations.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(store.export(), ensure_ascii=False, indent=2), encoding="utf-8")
        store.db.close()
        print(f"Exported observations: {output}")
    else:
        if not .5 <= args.threshold < 1:
            parser.error("Threshold must be in [0.5, 1)")
        rows = json.loads(Path(args.input).read_text(encoding="utf-8"))
        model, report = train(rows, args.threshold)
        output = Path(args.output or "data/model.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(model, indent=2, allow_nan=False), encoding="utf-8")
        output.with_suffix(".report.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
