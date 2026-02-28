"""Tests for CommandAuditor (auditor.py)."""

import pytest
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch


class TestCommandAuditor:
    def test_default_bin_path(self):
        from nanobot.agent.auditor import CommandAuditor

        auditor = CommandAuditor()
        assert auditor.bin_path == "opencode"

    def test_custom_bin_path(self):
        from nanobot.agent.auditor import CommandAuditor

        auditor = CommandAuditor(bin_path="/usr/local/bin/opencode")
        assert auditor.bin_path == "/usr/local/bin/opencode"

    def test_has_system_prompt(self):
        from nanobot.agent.auditor import CommandAuditor

        auditor = CommandAuditor()
        assert "SAFE" in auditor.system_prompt
        assert "UNSAFE" in auditor.system_prompt

    @pytest.mark.asyncio
    async def test_evaluate_returns_safe(self):
        from nanobot.agent.auditor import CommandAuditor

        # Mock a process that returns a JSON line with "SAFE"
        mock_stdout_lines = [
            json.dumps({"type": "text", "part": {"text": "SAFE"}}).encode() + b"\n"
        ]

        mock_process = MagicMock()
        mock_process.stdout = _aiter(mock_stdout_lines)
        mock_process.stderr = _aiter([])
        mock_process.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            auditor = CommandAuditor()
            result = await auditor.evaluate("echo hello")

        assert result == "SAFE"

    @pytest.mark.asyncio
    async def test_evaluate_returns_unsafe(self):
        from nanobot.agent.auditor import CommandAuditor

        mock_stdout_lines = [
            json.dumps({"type": "text", "part": {"text": "UNSAFE"}}).encode() + b"\n"
        ]

        mock_process = MagicMock()
        mock_process.stdout = _aiter(mock_stdout_lines)
        mock_process.stderr = _aiter([])
        mock_process.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            auditor = CommandAuditor()
            result = await auditor.evaluate("rm -rf /")

        assert result == "UNSAFE"

    @pytest.mark.asyncio
    async def test_evaluate_defaults_to_unsafe_on_ambiguous(self):
        from nanobot.agent.auditor import CommandAuditor

        mock_stdout_lines = [
            json.dumps({"type": "text", "part": {"text": "I cannot determine"}}).encode() + b"\n"
        ]

        mock_process = MagicMock()
        mock_process.stdout = _aiter(mock_stdout_lines)
        mock_process.stderr = _aiter([])
        mock_process.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            auditor = CommandAuditor()
            result = await auditor.evaluate("some ambiguous command")

        assert result == "UNSAFE"

    @pytest.mark.asyncio
    async def test_evaluate_on_subprocess_error_returns_unsafe(self):
        from nanobot.agent.auditor import CommandAuditor

        with patch(
            "asyncio.create_subprocess_exec", side_effect=FileNotFoundError("opencode not found")
        ):
            auditor = CommandAuditor()
            result = await auditor.evaluate("echo hello")

        assert result == "UNSAFE"

    @pytest.mark.asyncio
    async def test_evaluate_safe_with_surrounding_text(self):
        from nanobot.agent.auditor import CommandAuditor

        mock_stdout_lines = [
            json.dumps({"type": "text", "part": {"text": "This command is SAFE to run"}}).encode()
            + b"\n"
        ]

        mock_process = MagicMock()
        mock_process.stdout = _aiter(mock_stdout_lines)
        mock_process.stderr = _aiter([])
        mock_process.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            auditor = CommandAuditor()
            result = await auditor.evaluate("ls -la")

        assert result == "SAFE"

    @pytest.mark.asyncio
    async def test_evaluate_unsafe_takes_precedence_over_safe(self):
        """If both SAFE and UNSAFE appear, UNSAFE wins."""
        from nanobot.agent.auditor import CommandAuditor

        mock_stdout_lines = [
            json.dumps({"type": "text", "part": {"text": "Not SAFE, this is UNSAFE"}}).encode()
            + b"\n"
        ]

        mock_process = MagicMock()
        mock_process.stdout = _aiter(mock_stdout_lines)
        mock_process.stderr = _aiter([])
        mock_process.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            auditor = CommandAuditor()
            result = await auditor.evaluate("some command")

        assert result == "UNSAFE"

    @pytest.mark.asyncio
    async def test_evaluate_ignores_non_text_events(self):
        from nanobot.agent.auditor import CommandAuditor

        mock_stdout_lines = [
            json.dumps({"type": "tool_call", "part": {"name": "execute"}}).encode() + b"\n",
            json.dumps({"type": "text", "part": {"text": "SAFE"}}).encode() + b"\n",
        ]

        mock_process = MagicMock()
        mock_process.stdout = _aiter(mock_stdout_lines)
        mock_process.stderr = _aiter([])
        mock_process.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            auditor = CommandAuditor()
            result = await auditor.evaluate("echo hello")

        assert result == "SAFE"


def _aiter(items):
    """Create an async iterable from a list of bytes."""

    async def _gen():
        for item in items:
            yield item

    return _gen()
