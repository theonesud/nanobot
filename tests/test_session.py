import time

import pytest

from nanobot.session.manager import Session, SessionManager


class TestSession:
    def test_create_session(self):
        session = Session(key="slack:C123")
        assert session.key == "slack:C123"
        assert session.messages == []
        assert session.last_consolidated == 0

    def test_add_message(self):
        session = Session(key="test")
        session.add_message("user", "Hello")
        assert len(session.messages) == 1
        assert session.messages[0]["role"] == "user"
        assert session.messages[0]["content"] == "Hello"

    def test_add_message_has_timestamp(self):
        session = Session(key="test")
        session.add_message("user", "Hi")
        assert "timestamp" in session.messages[0]

    def test_add_message_extra_kwargs(self):
        session = Session(key="test")
        session.add_message("assistant", "Hi", tool_calls=[{"id": "x"}])
        assert "tool_calls" in session.messages[0]

    def test_get_history_returns_messages(self):
        session = Session(key="test")
        session.add_message("user", "Hello")
        session.add_message("assistant", "World")
        history = session.get_history()
        assert len(history) == 2

    def test_get_history_max_messages(self):
        session = Session(key="test")
        for i in range(20):
            session.add_message("user" if i % 2 == 0 else "assistant", f"msg{i}")
        history = session.get_history(max_messages=6)
        assert len(history) <= 6

    def test_get_history_starts_at_user_turn(self):
        session = Session(key="test")
        session.add_message("assistant", "First assistant")
        session.add_message("user", "Then user")
        history = session.get_history()
        assert history[0]["role"] == "user"

    def test_get_history_excludes_consolidated(self):
        session = Session(key="test")
        for i in range(10):
            session.add_message("user", f"msg{i}")
        session.last_consolidated = 8
        history = session.get_history()
        assert len(history) <= 2

    def test_get_history_preserves_tool_fields(self):
        session = Session(key="test")
        session.add_message("assistant", "", tool_calls=[{"id": "t1"}])
        session.add_message("user", "done")
        history = session.get_history()
        asst = next((m for m in history if m["role"] == "assistant"), None)
        if asst:
            assert "tool_calls" in asst

    def test_clear_resets_session(self):
        session = Session(key="test")
        session.add_message("user", "hello")
        session.add_message("assistant", "hi")
        session.last_consolidated = 2
        session.clear()
        assert session.messages == []
        assert session.last_consolidated == 0


class TestSessionManager:
    @pytest.fixture
    def manager(self, temp_workspace):
        return SessionManager(temp_workspace)

    def test_creates_session_dir(self, temp_workspace):
        SessionManager(temp_workspace)
        assert (temp_workspace / "sessions").exists()

    def test_get_or_create_new_session(self, manager):
        session = manager.get_or_create("slack:C001")
        assert session is not None
        assert session.key == "slack:C001"

    def test_get_or_create_returns_same_session(self, manager):
        s1 = manager.get_or_create("slack:C001")
        s2 = manager.get_or_create("slack:C001")
        assert s1 is s2

    def test_save_and_reload_session(self, manager, temp_workspace):
        session = manager.get_or_create("test:room1")
        session.add_message("user", "Hello")
        session.add_message("assistant", "World")
        manager.save(session)
        new_mgr = SessionManager(temp_workspace)
        reloaded = new_mgr.get_or_create("test:room1")
        assert len(reloaded.messages) == 2
        assert reloaded.messages[0]["content"] == "Hello"

    def test_save_persists_last_consolidated(self, manager, temp_workspace):
        session = manager.get_or_create("persist:test")
        session.add_message("user", "m1")
        session.add_message("user", "m2")
        session.last_consolidated = 1
        manager.save(session)
        new_mgr = SessionManager(temp_workspace)
        reloaded = new_mgr.get_or_create("persist:test")
        assert reloaded.last_consolidated == 1

    def test_save_persists_metadata(self, manager, temp_workspace):
        session = manager.get_or_create("meta:test")
        session.metadata["custom"] = "value"
        manager.save(session)
        new_mgr = SessionManager(temp_workspace)
        reloaded = new_mgr.get_or_create("meta:test")
        assert reloaded.metadata.get("custom") == "value"

    def test_invalidate_removes_from_cache(self, manager):
        s1 = manager.get_or_create("invalid:test")
        manager.invalidate("invalid:test")
        s2 = manager.get_or_create("invalid:test")
        assert s1 is not s2

    def test_list_sessions_returns_saved(self, manager):
        s1 = manager.get_or_create("list:room1")
        s1.add_message("user", "hi")
        manager.save(s1)
        s2 = manager.get_or_create("list:room2")
        s2.add_message("user", "yo")
        manager.save(s2)
        sessions = manager.list_sessions()
        keys = [s["key"] for s in sessions]
        assert "list:room1" in keys
        assert "list:room2" in keys

    def test_list_sessions_sorted_by_updated_at(self, manager):
        s1 = manager.get_or_create("sorted:a")
        s1.add_message("user", "old")
        manager.save(s1)
        time.sleep(0.01)
        s2 = manager.get_or_create("sorted:b")
        s2.add_message("user", "new")
        manager.save(s2)
        sessions = manager.list_sessions()
        keys = [s["key"] for s in sessions]
        assert keys.index("sorted:b") < keys.index("sorted:a")

    def test_get_or_create_missing_session_returns_fresh(self, manager):
        session = manager.get_or_create("new:fresh")
        assert session.messages == []

    def test_safe_filename_with_colons(self, temp_workspace):
        mgr = SessionManager(temp_workspace)
        session = mgr.get_or_create("channel:chat:room")
        session.add_message("user", "test")
        mgr.save(session)
        session_files = list((temp_workspace / "sessions").glob("*.jsonl"))
        assert len(session_files) == 1
        assert ":" not in session_files[0].name
