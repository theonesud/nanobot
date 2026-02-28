"""Comprehensive tests for filesystem tools (read_file, write_file, edit_file, list_dir)."""

import pytest
from pathlib import Path

from nanobot.agent.tools.filesystem import (
    ReadFileTool,
    WriteFileTool,
    EditFileTool,
    ListDirTool,
    _resolve_path,
)


class TestResolvePath:
    def test_absolute_path(self, tmp_path):
        result = _resolve_path(str(tmp_path))
        assert result == tmp_path.resolve()

    def test_relative_with_workspace(self, tmp_path):
        result = _resolve_path("subdir", workspace=tmp_path)
        assert result == (tmp_path / "subdir").resolve()

    def test_home_expansion(self):
        result = _resolve_path("~/test_nanobot_path")
        assert "~" not in str(result)

    def test_allowed_dir_inside(self, tmp_path):
        result = _resolve_path(str(tmp_path), allowed_dir=tmp_path)
        assert result == tmp_path.resolve()

    def test_allowed_dir_outside_raises(self, tmp_path):
        other = tmp_path.parent
        with pytest.raises(PermissionError, match="outside allowed directory"):
            _resolve_path("/etc/passwd", allowed_dir=tmp_path)


class TestReadFileTool:
    @pytest.mark.asyncio
    async def test_read_existing_file(self, tmp_path):
        f = tmp_path / "hello.txt"
        f.write_text("hello world")
        tool = ReadFileTool(workspace=tmp_path)
        result = await tool.execute("hello.txt")
        assert result == "hello world"

    @pytest.mark.asyncio
    async def test_read_missing_file(self, tmp_path):
        tool = ReadFileTool(workspace=tmp_path)
        result = await tool.execute("missing.txt")
        assert "Error" in result
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_read_directory_as_file(self, tmp_path):
        d = tmp_path / "subdir"
        d.mkdir()
        tool = ReadFileTool(workspace=tmp_path)
        result = await tool.execute("subdir")
        assert "Error" in result
        assert "not a file" in result.lower()

    @pytest.mark.asyncio
    async def test_read_outside_allowed_dir(self, tmp_path):
        tool = ReadFileTool(workspace=tmp_path, allowed_dir=tmp_path)
        result = await tool.execute("/etc/passwd")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_read_absolute_path(self, tmp_path):
        f = tmp_path / "abs.txt"
        f.write_text("absolute")
        tool = ReadFileTool()
        result = await tool.execute(str(f))
        assert result == "absolute"

    def test_schema_name(self):
        assert ReadFileTool().name == "read_file"

    def test_schema_has_path(self):
        params = ReadFileTool().parameters
        assert "path" in params["properties"]


class TestWriteFileTool:
    @pytest.mark.asyncio
    async def test_write_new_file(self, tmp_path):
        tool = WriteFileTool(workspace=tmp_path)
        result = await tool.execute("output.txt", "content")
        assert "Successfully" in result
        assert (tmp_path / "output.txt").read_text() == "content"

    @pytest.mark.asyncio
    async def test_write_creates_directories(self, tmp_path):
        tool = WriteFileTool(workspace=tmp_path)
        result = await tool.execute("nested/dir/file.txt", "data")
        assert "Successfully" in result
        assert (tmp_path / "nested" / "dir" / "file.txt").exists()

    @pytest.mark.asyncio
    async def test_write_overwrites_existing(self, tmp_path):
        f = tmp_path / "exist.txt"
        f.write_text("old")
        tool = WriteFileTool(workspace=tmp_path)
        await tool.execute("exist.txt", "new")
        assert f.read_text() == "new"

    @pytest.mark.asyncio
    async def test_write_outside_allowed(self, tmp_path):
        tool = WriteFileTool(workspace=tmp_path, allowed_dir=tmp_path)
        result = await tool.execute("/etc/hacked.txt", "oops")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_write_empty_content(self, tmp_path):
        tool = WriteFileTool(workspace=tmp_path)
        result = await tool.execute("empty.txt", "")
        assert "Successfully" in result
        assert (tmp_path / "empty.txt").read_text() == ""

    def test_schema_name(self):
        assert WriteFileTool().name == "write_file"

    def test_schema_requires_path_and_content(self):
        params = WriteFileTool().parameters
        assert "path" in params["required"]
        assert "content" in params["required"]


class TestEditFileTool:
    @pytest.mark.asyncio
    async def test_edit_simple_replacement(self, tmp_path):
        f = tmp_path / "edit.txt"
        f.write_text("hello world")
        tool = EditFileTool(workspace=tmp_path)
        result = await tool.execute("edit.txt", "hello", "goodbye")
        assert "Successfully" in result
        assert f.read_text() == "goodbye world"

    @pytest.mark.asyncio
    async def test_edit_missing_file(self, tmp_path):
        tool = EditFileTool(workspace=tmp_path)
        result = await tool.execute("nope.txt", "x", "y")
        assert "Error" in result
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_edit_old_text_not_found(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("the quick brown fox")
        tool = EditFileTool(workspace=tmp_path)
        result = await tool.execute("f.txt", "lazy dog", "cat")
        assert "Error" in result
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_edit_multiple_occurrences_warns(self, tmp_path):
        f = tmp_path / "dup.txt"
        f.write_text("a a a")
        tool = EditFileTool(workspace=tmp_path)
        result = await tool.execute("dup.txt", "a", "b")
        assert "Warning" in result or "times" in result

    @pytest.mark.asyncio
    async def test_edit_only_replaces_first(self, tmp_path):
        """When there's exactly one match, it replaces just once."""
        f = tmp_path / "one.txt"
        f.write_text("foo bar")
        tool = EditFileTool(workspace=tmp_path)
        result = await tool.execute("one.txt", "foo", "baz")
        assert "Successfully" in result
        assert f.read_text() == "baz bar"

    @pytest.mark.asyncio
    async def test_edit_outside_allowed(self, tmp_path):
        tool = EditFileTool(workspace=tmp_path, allowed_dir=tmp_path)
        result = await tool.execute("/etc/passwd", "x", "y")
        assert "Error" in result

    def test_schema_name(self):
        assert EditFileTool().name == "edit_file"

    def test_schema_requires_path_old_new(self):
        params = EditFileTool().parameters
        for field in ["path", "old_text", "new_text"]:
            assert field in params["required"]


class TestListDirTool:
    @pytest.mark.asyncio
    async def test_list_directory(self, tmp_path):
        (tmp_path / "file.txt").write_text("x")
        (tmp_path / "subdir").mkdir()
        tool = ListDirTool(workspace=tmp_path)
        result = await tool.execute(".")
        assert "file.txt" in result
        assert "subdir" in result

    @pytest.mark.asyncio
    async def test_list_missing_directory(self, tmp_path):
        tool = ListDirTool(workspace=tmp_path)
        result = await tool.execute("ghost")
        assert "Error" in result
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_list_file_as_directory(self, tmp_path):
        (tmp_path / "file.txt").write_text("x")
        tool = ListDirTool(workspace=tmp_path)
        result = await tool.execute("file.txt")
        assert "Error" in result
        assert "not a directory" in result.lower()

    @pytest.mark.asyncio
    async def test_list_empty_directory(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        tool = ListDirTool(workspace=tmp_path)
        result = await tool.execute("empty")
        assert "empty" in result.lower()

    @pytest.mark.asyncio
    async def test_list_shows_icons(self, tmp_path):
        (tmp_path / "f.txt").write_text("")
        (tmp_path / "d").mkdir()
        tool = ListDirTool(workspace=tmp_path)
        result = await tool.execute(".")
        assert "📁" in result
        assert "📄" in result

    def test_schema_name(self):
        assert ListDirTool().name == "list_dir"
