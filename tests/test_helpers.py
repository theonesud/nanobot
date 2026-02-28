"""Tests for utils/helpers.py."""

import pytest
from pathlib import Path
import tempfile

from nanobot.utils.helpers import ensure_dir, safe_filename, timestamp


class TestEnsureDir:
    """Tests for ensure_dir helper."""

    def test_ensure_dir_creates_directory(self, temp_workspace):
        """Test that ensure_dir creates a directory."""
        new_dir = temp_workspace / "new_dir"
        result = ensure_dir(new_dir)
        assert result.exists()
        assert result.is_dir()

    def test_ensure_dir_existing_directory(self, temp_workspace):
        """Test that ensure_dir works with existing directory."""
        new_dir = temp_workspace / "existing"
        new_dir.mkdir()
        result = ensure_dir(new_dir)
        assert result.exists()

    def test_ensure_dir_nested(self, temp_workspace):
        """Test creating nested directories."""
        nested = temp_workspace / "a" / "b" / "c"
        result = ensure_dir(nested)
        assert result.exists()
        assert result.is_dir()


class TestSafeFilename:
    """Tests for safe_filename helper."""

    def test_safe_filename_simple(self):
        """Test safe filename with simple string."""
        result = safe_filename("test_file.txt")
        assert result == "test_file.txt"

    def test_safe_filename_with_unsafe_chars(self):
        """Test safe filename with unsafe characters."""
        result = safe_filename("file<name>.txt")
        assert "<" not in result
        assert ">" not in result

    def test_safe_filename_strips(self):
        """Test safe filename strips whitespace."""
        result = safe_filename("  filename  ")
        assert result == "filename"


class TestTimestamp:
    """Tests for timestamp helper."""

    def test_timestamp_format(self):
        """Test timestamp returns ISO format."""
        ts = timestamp()
        assert "T" in ts  # ISO format contains T
        assert ":" in ts  # Contains time separator
