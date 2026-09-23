import json
import sqlite3
from pathlib import Path
from datetime import timedelta
from .market import stamp


class Store:
    def __init__(self, path):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS ideas (
                id TEXT PRIMARY KEY, symbol TEXT NOT NULL, trade_date TEXT NOT NULL,
                created TEXT NOT NULL, expires TEXT NOT NULL, state TEXT NOT NULL,
                data TEXT NOT NULL, checked TEXT NOT NULL, target1 INTEGER DEFAULT 0,
                closed TEXT, result_r REAL);
            CREATE TABLE IF NOT EXISTS outbox (
                id TEXT PRIMARY KEY, idea_id TEXT, body TEXT NOT NULL, expires TEXT,
                state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                retry_after TEXT, created TEXT NOT NULL);
        """)

    def active(self):
        return list(self.db.execute("SELECT * FROM ideas WHERE state='active' ORDER BY created"))

    def can_add(self, c, config):
        if len(self.active()) >= config.max_active:
            return False
        count = self.db.execute("SELECT count(*) FROM ideas WHERE trade_date=?", (c.trade_date,)).fetchone()[0]
        losses = self.db.execute("SELECT coalesce(sum(min(coalesce(result_r,-1),0)),0) FROM ideas WHERE trade_date=? AND state NOT IN ('active','cancelled')", (c.trade_date,)).fetchone()[0]
        recent = self.db.execute("SELECT 1 FROM ideas WHERE symbol=? AND (state='active' OR created>?)",
                                 (c.symbol, (stamp(c.created) - timedelta(hours=2)).isoformat())).fetchone()
        return count < config.max_daily and -losses < config.max_loss_r and not recent

    def add(self, c, text):
        identifier = f"{c.symbol}:{c.session_key}:{c.bar_time}"
        with self.db:
            cur = self.db.execute("INSERT OR IGNORE INTO ideas(id,symbol,trade_date,created,expires,state,data,checked) VALUES (?,?,?,?,?,'active',?,?)",
                                  (identifier, c.symbol, c.trade_date, c.created, c.expires, json.dumps(c.dict()), c.created))
            if not cur.rowcount:
                return False
            self.db.execute("INSERT INTO outbox(id,idea_id,body,expires,created) VALUES (?,?,?,?,?)",
                            (identifier + ":entry", identifier, text, (stamp(c.created) + timedelta(seconds=60)).isoformat(), c.created))
        return True

    def event(self, row, event, text, now, result_r=None):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO outbox(id,idea_id,body,created) VALUES (?,?,?,?)",
                            (row["id"] + ":" + event, row["id"], text, now.isoformat()))
            if event == "target1":
                self.db.execute("UPDATE ideas SET target1=1,checked=? WHERE id=?", (now.isoformat(), row["id"]))
            else:
                self.db.execute("UPDATE ideas SET state=?,closed=?,result_r=? WHERE id=?", (event, now.isoformat(), result_r, row["id"]))

    def pending(self, now):
        return list(self.db.execute("SELECT * FROM outbox WHERE state='pending' AND (retry_after IS NULL OR retry_after<=?) ORDER BY created LIMIT 10", (now.isoformat(),)))

    def delivered(self, item):
        with self.db:
            self.db.execute("UPDATE outbox SET state='sent' WHERE id=?", (item["id"],))

    def failed(self, item, now):
        delay = min(300, 2 ** min(item["attempts"] + 1, 8))
        with self.db:
            self.db.execute("UPDATE outbox SET attempts=attempts+1,retry_after=? WHERE id=?", ((now + timedelta(seconds=delay)).isoformat(), item["id"]))

    def cancel_expired_entry(self, item):
        with self.db:
            self.db.execute("UPDATE outbox SET state='expired' WHERE id=?", (item["id"],))
            self.db.execute("UPDATE ideas SET state='cancelled' WHERE id=? AND state='active'", (item["idea_id"],))

    def entry_delivered(self, idea_id):
        row = self.db.execute("SELECT state FROM outbox WHERE id=?", (idea_id + ":entry",)).fetchone()
        return row and row[0] == "sent"

    def touch(self, idea_id, now):
        with self.db:
            self.db.execute("UPDATE ideas SET checked=? WHERE id=?", (now.isoformat(), idea_id))

    def export(self):
        result = []
        for row in self.db.execute("SELECT * FROM ideas WHERE result_r IS NOT NULL AND state NOT IN ('unknown','cancelled') ORDER BY created"):
            data = json.loads(row["data"])
            data.update(closed=row["closed"], result_r=row["result_r"], outcome=row["state"])
            result.append(data)
        return result
