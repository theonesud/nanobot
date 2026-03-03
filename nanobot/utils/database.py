import sqlite3
import time
from datetime import datetime
from datetime import time as dt_time
from pathlib import Path

from loguru import logger


class Database:
    def __init__(self, workspace: Path):
        self.db_path = workspace / "nanobot.db"
        self._init_db()

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(
                "\n                CREATE TABLE IF NOT EXISTS task_costs (\n                    id INTEGER PRIMARY KEY AUTOINCREMENT,\n                    session_id TEXT,\n                    provider TEXT,\n                    model TEXT,\n                    tokens_prompt INTEGER,\n                    tokens_completion INTEGER,\n                    cost_usd REAL,\n                    timestamp INTEGER\n                )\n            "
            )
            conn.execute(
                "\n                CREATE TABLE IF NOT EXISTS traces (\n                    id INTEGER PRIMARY KEY AUTOINCREMENT,\n                    session_id TEXT,\n                    event_type TEXT,\n                    data TEXT,\n                    timestamp INTEGER\n                )\n            "
            )
            conn.execute(
                "\n                CREATE TABLE IF NOT EXISTS active_crons (\n                    id INTEGER PRIMARY KEY AUTOINCREMENT,\n                    schedule_expression TEXT,\n                    opencode_prompt TEXT,\n                    slack_channel_id TEXT,\n                    enabled BOOLEAN DEFAULT 1\n                )\n            "
            )
            conn.commit()

    def log_trace(self, session_id: str, event_type: str, data: dict) -> None:
        try:
            import json

            def _ser(obj):
                if isinstance(obj, (datetime, Path)):
                    return str(obj)
                try:
                    return dict(obj)
                except Exception:
                    return str(obj)

            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO traces (session_id, event_type, data, timestamp) VALUES (?, ?, ?, ?)",
                    (session_id, event_type, json.dumps(data, default=_ser), int(time.time())),
                )
                conn.commit()
        except Exception as e:
            logger.error("Failed to log trace: {}", e)

    def log_cost(
        self,
        session_id: str,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost: float = 0.0,
    ) -> None:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO task_costs (session_id, provider, model, tokens_prompt, tokens_completion, cost_usd, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        session_id,
                        provider,
                        model,
                        prompt_tokens,
                        completion_tokens,
                        cost,
                        int(time.time()),
                    ),
                )
                conn.commit()
        except Exception as e:
            logger.error("Failed to log task cost: {}", e)

    def get_daily_cost(self) -> float:
        start_of_day = int(datetime.combine(datetime.now(), dt_time.min).timestamp())
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "SELECT SUM(cost_usd) FROM task_costs WHERE timestamp >= ?", (start_of_day,)
                )
                row = cursor.fetchone()
                return row[0] if row and row[0] else 0.0
        except Exception as e:
            logger.error("Failed to get daily cost: {}", e)
            return 0.0
