import pytest

from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest


class TestLLMResponse:
    def test_response_creation(self):
        response = LLMResponse(content="Hello!")
        assert response.content == "Hello!"
        assert response.has_tool_calls is False
        assert response.tool_calls == []

    def test_response_with_tool_calls(self):
        tool_calls = [
            ToolCallRequest(id="call_1", name="read_file", arguments={"file_path": "/test.txt"})
        ]
        response = LLMResponse(content="Reading file...", tool_calls=tool_calls)
        assert response.has_tool_calls is True
        assert len(response.tool_calls) == 1

    def test_response_with_usage(self):
        response = LLMResponse(
            content="Test", usage={"prompt_tokens": 100, "completion_tokens": 50}
        )
        assert response.usage["prompt_tokens"] == 100
        assert response.usage["completion_tokens"] == 50


class TestToolCallRequest:
    def test_tool_call_creation(self):
        call = ToolCallRequest(
            id="call_123", name="read_file", arguments={"file_path": "/test.txt"}
        )
        assert call.id == "call_123"
        assert call.name == "read_file"
        assert call.arguments["file_path"] == "/test.txt"


class TestLLMProvider:
    def test_provider_init(self):

        class MockProvider(LLMProvider):
            async def chat(self, messages, **kwargs):
                return LLMResponse(content="Mock response")

            def get_default_model(self):
                return "gpt-4"

        provider = MockProvider(api_key="test-key")
        assert provider.api_key == "test-key"

    @pytest.mark.asyncio
    async def test_chat_call(self):

        class TestProvider(LLMProvider):
            async def chat(self, messages, **kwargs):
                return LLMResponse(content="Response")

            def get_default_model(self):
                return "test"

        provider = TestProvider(api_key="key")
        messages = [{"role": "user", "content": "Hello"}]
        response = await provider.chat(messages)
        assert response.content == "Response"

    def test_sanitize_empty_content(self):
        messages = [{"role": "user", "content": "Hello"}, {"role": "assistant", "content": ""}]
        result = LLMProvider._sanitize_empty_content(messages)
        assert result[1]["content"] == "(empty)"
