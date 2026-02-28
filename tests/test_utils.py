"""Tests for utils/helpers.py."""

import pytest
from pathlib import Path

from nanobot.utils.helpers import (
    ensure_dir,
    get_data_path,
    get_workspace_path,
    timestamp,
    safe_filename,
    sync_workspace_templates,
)


class TestEnsureDir:
    def test_creates_directory(self, tmp_path):
        new_dir = tmp_path / "new" / "nested"
        result = ensure_dir(new_dir)
        assert new_dir.exists()
        assert new_dir.is_dir()
        assert result == new_dir

    def test_existing_directory_ok(self, tmp_path):
        result = ensure_dir(tmp_path)
        assert result == tmp_path


class TestTimestamp:
    def test_returns_iso_string(self):
        ts = timestamp()
        assert isinstance(ts, str)
        # Should parse as datetime
        from datetime import datetime

        parsed = datetime.fromisoformat(ts)
        assert parsed is not None


class TestSafeFilename:
    def test_replaces_colon(self):
        result = safe_filename("channel:room")
        assert ":" not in result

    def test_replaces_slash(self):
        result = safe_filename("path/component")
        assert "/" not in result

    def test_replaces_backslash(self):
        result = safe_filename("win\\path")
        assert "\\" not in result

    def test_replaces_angle_brackets(self):
        result = safe_filename("a<b>c")
        assert "<" not in result
        assert ">" not in result

    def test_replaces_question_mark(self):
        result = safe_filename("query?key=val")
        assert "?" not in result

    def test_replaces_star(self):
        result = safe_filename("glob*")
        assert "*" not in result

    def test_replaces_quote(self):
        result = safe_filename('"quoted"')
        assert '"' not in result

    def test_preserves_normal_chars(self):
        result = safe_filename("normal-file_name.txt")
        assert result == "normal-file_name.txt"

    def test_strips_whitespace(self):
        result = safe_filename("  spaced  ")
        assert result == "spaced"


class TestGetWorkspacePath:
    def test_returns_path(self):
        p = get_workspace_path()
        assert isinstance(p, Path)
        assert p.exists()

    def test_custom_workspace(self, tmp_path):
        p = get_workspace_path(str(tmp_path / "custom_ws"))
        assert p.exists()
        assert p.name == "custom_ws"


class TestSyncWorkspaceTemplates:
    def test_creates_memory_file(self, tmp_path):
        result = sync_workspace_templates(tmp_path, silent=True)
        memory_file = tmp_path / "memory" / "MEMORY.md"
        history_file = tmp_path / "memory" / "HISTORY.md"
        assert memory_file.exists() or history_file.exists()

    def test_creates_skills_dir(self, tmp_path):
        sync_workspace_templates(tmp_path, silent=True)
        assert (tmp_path / "skills").exists()

    def test_does_not_overwrite_existing(self, tmp_path):
        memory_file = tmp_path / "memory" / "MEMORY.md"
        memory_file.parent.mkdir(parents=True, exist_ok=True)
        memory_file.write_text("custom content")

        sync_workspace_templates(tmp_path, silent=True)
        # Should still contain our text, not overwritten
        assert "custom content" in memory_file.read_text()

    def test_returns_list_of_created_files(self, tmp_path):
        result = sync_workspace_templates(tmp_path, silent=True)
        assert isinstance(result, list)

    def test_second_call_creates_nothing(self, tmp_path):
        sync_workspace_templates(tmp_path, silent=True)
        result2 = sync_workspace_templates(tmp_path, silent=True)
        assert result2 == []
