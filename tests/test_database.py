import sqlite3

import pytest

from nanobot.utils.database import Database


class TestDatabase:
    @pytest.fixture
    def db(self, temp_workspace):
        return Database(temp_workspace)

    def test_database_initialization(self, db, temp_workspace):
        assert db.db_path.exists()
        assert db.db_path == temp_workspace / "nanobot.db"

    def test_task_costs_table_exists(self, db):
        with sqlite3.connect(db.db_path) as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='task_costs'"
            )
            assert cursor.fetchone() is not None

    def test_active_crons_table_exists(self, db):
        with sqlite3.connect(db.db_path) as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='active_crons'"
            )
            assert cursor.fetchone() is not None

    def test_log_cost(self, db):
        db.log_cost(
            session_id="test-session",
            provider="openai",
            model="gpt-4",
            prompt_tokens=1000,
            completion_tokens=500,
            cost=0.03,
        )
        with sqlite3.connect(db.db_path) as conn:
            cursor = conn.execute("SELECT * FROM task_costs WHERE session_id = 'test-session'")
            row = cursor.fetchone()
            assert row is not None
            assert row[1] == "test-session"
            assert row[2] == "openai"
            assert row[3] == "gpt-4"
            assert row[4] == 1000
            assert row[5] == 500
            assert row[6] == 0.03

    def test_log_cost_defaults(self, db):
        db.log_cost(
            session_id="test-session-2",
            provider="anthropic",
            model="claude-3",
            prompt_tokens=500,
            completion_tokens=250,
        )
        with sqlite3.connect(db.db_path) as conn:
            cursor = conn.execute(
                "SELECT cost_usd FROM task_costs WHERE session_id = 'test-session-2'"
            )
            row = cursor.fetchone()
            assert row[0] == 0.0

    def test_get_daily_cost(self, db):
        db.log_cost(
            session_id="s1",
            provider="openai",
            model="gpt-4",
            prompt_tokens=1000,
            completion_tokens=500,
            cost=1.5,
        )
        db.log_cost(
            session_id="s2",
            provider="anthropic",
            model="claude-3",
            prompt_tokens=500,
            completion_tokens=250,
            cost=0.75,
        )
        daily_cost = db.get_daily_cost()
        assert daily_cost == 2.25

    def test_get_daily_cost_empty(self, db):
        cost = db.get_daily_cost()
        assert cost == 0.0

    def test_multiple_sessions(self, db):
        db.log_cost("session1", "openai", "gpt-4", 100, 50, 0.01)
        db.log_cost("session2", "anthropic", "claude-3", 200, 100, 0.02)
        db.log_cost("session3", "deepseek", "deepseek-chat", 150, 75, 0.005)
        with sqlite3.connect(db.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM task_costs")
            count = cursor.fetchone()[0]
            assert count == 3
