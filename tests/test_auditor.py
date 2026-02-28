"""Tests for CommandAuditor (auditor.py)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.auditor import CommandAuditor
from nanobot.providers.base import LLMResponse


class TestCommandAuditor:
    def test_has_system_prompt(self):
        provider = MagicMock()
        auditor = CommandAuditor(provider=provider, model="test-model")
        assert "SAFE" in auditor.system_prompt
        assert "UNSAFE" in auditor.system_prompt

    @pytest.mark.asyncio
    async def test_evaluate_returns_safe(self):
        provider = MagicMock()
        provider.chat = AsyncMock(return_value=LLMResponse(content="SAFE", finish_reason="stop"))

        auditor = CommandAuditor(provider=provider, model="test-model")
        result = await auditor.evaluate("echo hello")

        assert result == "SAFE"
        provider.chat.assert_called_once()
        assert "echo hello" in provider.chat.call_args[1]["messages"][1]["content"]

    @pytest.mark.asyncio
    async def test_evaluate_returns_unsafe(self):
        provider = MagicMock()
        provider.chat = AsyncMock(return_value=LLMResponse(content="UNSAFE", finish_reason="stop"))

        auditor = CommandAuditor(provider=provider, model="test-model")
        result = await auditor.evaluate("rm -rf /")

        assert result == "UNSAFE"

    @pytest.mark.asyncio
    async def test_evaluate_defaults_to_unsafe_on_ambiguous(self):
        provider = MagicMock()
        provider.chat = AsyncMock(
            return_value=LLMResponse(content="I cannot determine", finish_reason="stop")
        )

        auditor = CommandAuditor(provider=provider, model="test-model")
        result = await auditor.evaluate("some ambiguous command")

        assert result == "UNSAFE"

    @pytest.mark.asyncio
    async def test_evaluate_on_provider_error_returns_unsafe(self):
        provider = MagicMock()
        provider.chat = AsyncMock(side_effect=Exception("API Error"))

        auditor = CommandAuditor(provider=provider, model="test-model")
        result = await auditor.evaluate("echo hello")

        assert result == "UNSAFE"

    @pytest.mark.asyncio
    async def test_evaluate_safe_with_surrounding_text(self):
        provider = MagicMock()
        provider.chat = AsyncMock(
            return_value=LLMResponse(content="This command is SAFE to run", finish_reason="stop")
        )

        auditor = CommandAuditor(provider=provider, model="test-model")
        result = await auditor.evaluate("ls -la")

        assert result == "SAFE"

    @pytest.mark.asyncio
    async def test_evaluate_unsafe_takes_precedence_over_safe(self):
        """If both SAFE and UNSAFE appear, UNSAFE wins."""
        provider = MagicMock()
        provider.chat = AsyncMock(
            return_value=LLMResponse(content="Not SAFE, this is UNSAFE", finish_reason="stop")
        )

        auditor = CommandAuditor(provider=provider, model="test-model")
        result = await auditor.evaluate("some command")

        assert result == "UNSAFE"
