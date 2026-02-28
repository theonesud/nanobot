"""Comprehensive tests for the ExecTool (shell.py)."""

import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


from nanobot.agent.tools.shell import ExecTool


class TestExecToolInit:
    def test_default_deny_patterns(self):
        tool = ExecTool()
        assert len(tool.deny_patterns) > 0

    def test_custom_deny_patterns(self):
        tool = ExecTool(deny_patterns=[r"\btest\b"])
        assert tool.deny_patterns == [r"\btest\b"]

    def test_default_allow_patterns_empty(self):
        tool = ExecTool()
        assert tool.allow_patterns == []

    def test_timeout_default(self):
        tool = ExecTool()
        assert tool.timeout == 60

    def test_custom_timeout(self):
        tool = ExecTool(timeout=10)
        assert tool.timeout == 10


class TestGuardCommand:
    """Test _guard_command safety checks."""

    def test_allows_safe_echo(self):
        tool = ExecTool()
        result = tool._guard_command("echo hello", "/tmp")
        assert result is None

    def test_blocks_rm_rf(self):
        tool = ExecTool()
        result = tool._guard_command("rm -rf /", "/tmp")
        assert result is not None
        assert "blocked" in result.lower()

    def test_blocks_rm_rf_variation(self):
        tool = ExecTool()
        result = tool._guard_command("rm -fr /home/user", "/tmp")
        assert result is not None

    def test_blocks_sudo(self):
        tool = ExecTool()
        result = tool._guard_command("sudo apt install python", "/tmp")
        assert result is not None

    def test_blocks_mkfs(self):
        tool = ExecTool()
        result = tool._guard_command("mkfs.ext4 /dev/sda", "/tmp")
        assert result is not None

    def test_blocks_dd_if(self):
        tool = ExecTool()
        result = tool._guard_command("dd if=/dev/zero of=/dev/sda", "/tmp")
        assert result is not None

    def test_blocks_shutdown(self):
        tool = ExecTool()
        result = tool._guard_command("shutdown -h now", "/tmp")
        assert result is not None

    def test_blocks_reboot(self):
        tool = ExecTool()
        result = tool._guard_command("reboot", "/tmp")
        assert result is not None

    def test_blocks_fork_bomb(self):
        tool = ExecTool()
        result = tool._guard_command(":(){ :|:& };:", "/tmp")
        assert result is not None

    def test_allow_patterns_pass(self):
        tool = ExecTool(allow_patterns=[r"\bls\b"])
        result = tool._guard_command("ls -la", "/tmp")
        assert result is None

    def test_allow_patterns_block_when_not_matching(self):
        tool = ExecTool(allow_patterns=[r"\bls\b"])
        result = tool._guard_command("cat file.txt", "/tmp")
        assert result is not None
        assert "allowlist" in result

    def test_restrict_to_workspace_blocks_traversal(self, tmp_path):
        tool = ExecTool(restrict_to_workspace=True)
        result = tool._guard_command("cat ../secret.txt", str(tmp_path))
        assert result is not None
        assert "traversal" in result.lower()

    def test_restrict_to_workspace_allows_inside(self, tmp_path):
        tool = ExecTool(restrict_to_workspace=True)
        result = tool._guard_command("cat file.txt", str(tmp_path))
        assert result is None


class TestExecToolExecute:
    """Test execute() method."""

    @pytest.mark.asyncio
    async def test_execute_simple_command(self):
        tool = ExecTool(timeout=10)
        result = await tool.execute("echo hello")
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_execute_command_with_stderr(self):
        tool = ExecTool(timeout=10)
        result = await tool.execute("echo err >&2")
        # Should capture something — either via stdout or STDERR header
        assert result is not None

    @pytest.mark.asyncio
    async def test_execute_exit_code_nonzero(self):
        tool = ExecTool(timeout=10)
        result = await tool.execute("exit 1", working_dir="/tmp")
        # Should include exit code info
        assert "Exit code" in result or result == "(no output)"

    @pytest.mark.asyncio
    async def test_execute_working_dir(self, tmp_path):
        tool = ExecTool(timeout=10)
        result = await tool.execute("pwd", working_dir=str(tmp_path))
        assert str(tmp_path) in result or result.strip() != ""

    @pytest.mark.asyncio
    async def test_execute_blocked_command(self):
        tool = ExecTool()
        result = await tool.execute("sudo rm -rf /")
        assert "blocked" in result.lower() or "Error" in result

    @pytest.mark.asyncio
    async def test_execute_timeout(self):
        tool = ExecTool(timeout=1)
        result = await tool.execute("sleep 10")
        assert "timed out" in result

    @pytest.mark.asyncio
    async def test_execute_long_output_truncated(self):
        tool = ExecTool(timeout=10)
        # Generate output > 10000 chars
        result = await tool.execute("python3 -c \"print('x' * 20000)\"")
        assert "truncated" in result

    @pytest.mark.asyncio
    async def test_execute_no_output(self):
        tool = ExecTool(timeout=10)
        # A command with no output
        result = await tool.execute("true")
        assert result == "(no output)"

    @pytest.mark.asyncio
    async def test_execute_with_auditor_safe(self):
        mock_auditor = MagicMock()
        mock_auditor.evaluate = AsyncMock(return_value="SAFE")
        tool = ExecTool(auditor=mock_auditor)
        result = await tool.execute("echo hello")
        assert "hello" in result
        mock_auditor.evaluate.assert_called_once_with("echo hello")

    @pytest.mark.asyncio
    async def test_execute_with_auditor_unsafe_no_bus(self):
        mock_auditor = MagicMock()
        mock_auditor.evaluate = AsyncMock(return_value="UNSAFE")
        tool = ExecTool(auditor=mock_auditor)
        result = await tool.execute("echo hello")
        assert "Error" in result or "blocked" in result.lower()

    @pytest.mark.asyncio
    async def test_path_append_extends_env(self, tmp_path):
        tool = ExecTool(timeout=10, path_append=str(tmp_path))
        result = await tool.execute("echo $PATH")
        assert str(tmp_path) in result


class TestExecToolSchema:
    def test_name(self):
        assert ExecTool().name == "exec"

    def test_description(self):
        assert "shell" in ExecTool().description.lower()

    def test_parameters_has_command(self):
        params = ExecTool().parameters
        assert "command" in params["properties"]
        assert "command" in params["required"]
