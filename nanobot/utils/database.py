"""SQLite database utilities for cost tracking and proactive tasks."""

import sqlite3
import time
from pathlib import Path

from loguru import logger


class Database:
    """Manages Nanobot's SQLite database."""

    def __init__(self, workspace: Path):
        self.db_path = workspace / "nanobot.db"
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS task_costs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    provider TEXT,
                    model TEXT,
                    tokens_prompt INTEGER,
                    tokens_completion INTEGER,
                    cost_usd REAL,
                    timestamp INTEGER
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS active_crons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    schedule_expression TEXT,
                    opencode_prompt TEXT,
                    slack_channel_id TEXT,
                    enabled BOOLEAN DEFAULT 1
                )
            """)
            conn.commit()

    def log_cost(
        self,
        session_id: str,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost: float = 0.0,
    ) -> None:
        """Log the cost of a single LLM call."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO task_costs (session_id, provider, model, tokens_prompt, tokens_completion, cost_usd, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
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
        """Calculate total cost for the current day."""
        start_of_day = int(time.time() // 86400 * 86400)
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
