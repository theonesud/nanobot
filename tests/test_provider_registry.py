"""Comprehensive tests for provider registry."""

import pytest

from nanobot.providers.registry import (
    PROVIDERS,
    ProviderSpec,
    find_by_model,
    find_by_name,
    find_gateway,
)


class TestProviderSpec:
    def test_label_uses_display_name(self):
        spec = ProviderSpec(
            name="test", keywords=("test",), env_key="TEST_KEY", display_name="Test Provider"
        )
        assert spec.label == "Test Provider"

    def test_label_falls_back_to_title(self):
        spec = ProviderSpec(name="mytest", keywords=("mytest",), env_key="KEY")
        assert spec.label == "Mytest"

    def test_frozen_dataclass(self):
        spec = ProviderSpec(name="x", keywords=("x",), env_key="KEY")
        with pytest.raises(Exception):
            spec.name = "y"


class TestFindByModel:
    def test_finds_anthropic_by_claude(self):
        spec = find_by_model("claude-3-opus")
        assert spec is not None
        assert spec.name == "anthropic"

    def test_finds_openai_by_gpt(self):
        spec = find_by_model("gpt-4o")
        assert spec is not None
        assert spec.name == "openai"

    def test_finds_deepseek(self):
        spec = find_by_model("deepseek-chat")
        assert spec is not None
        assert spec.name == "deepseek"

    def test_finds_gemini(self):
        spec = find_by_model("gemini-pro")
        assert spec is not None
        assert spec.name == "gemini"

    def test_finds_dashscope_by_qwen(self):
        spec = find_by_model("qwen-max")
        assert spec is not None
        assert spec.name == "dashscope"

    def test_finds_moonshot_by_kimi(self):
        spec = find_by_model("kimi-k2.5")
        assert spec is not None
        assert spec.name == "moonshot"

    def test_returns_none_for_unknown(self):
        spec = find_by_model("totally-unknown-model-xyz")
        assert spec is None

    def test_prefix_beats_keyword(self):
        """github-copilot/...-codex should match github_copilot, not openai_codex."""
        spec = find_by_model("github-copilot/gpt-5.3-codex")
        assert spec is not None
        assert spec.name == "github_copilot"

    def test_openai_codex_prefix(self):
        spec = find_by_model("openai-codex/gpt-5.1-codex")
        assert spec is not None
        assert spec.name == "openai_codex"

    def test_case_insensitive_matching(self):
        spec = find_by_model("Claude-3-Opus")
        assert spec is not None
        assert spec.name == "anthropic"

    def test_dotted_prefix_gemini(self):
        spec = find_by_model("gemini/gemini-1.5-pro")
        assert spec is not None
        assert spec.name == "gemini"

    def test_zhipu_by_glm(self):
        spec = find_by_model("glm-4")
        assert spec is not None
        assert spec.name == "zhipu"

    def test_groq_keyword(self):
        spec = find_by_model("groq-llama3")
        assert spec is not None
        assert spec.name == "groq"


class TestFindByName:
    def test_finds_anthropic(self):
        spec = find_by_name("anthropic")
        assert spec is not None
        assert spec.name == "anthropic"

    def test_finds_openai(self):
        spec = find_by_name("openai")
        assert spec is not None

    def test_finds_openrouter(self):
        spec = find_by_name("openrouter")
        assert spec is not None
        assert spec.is_gateway

    def test_returns_none_for_missing(self):
        assert find_by_name("nonexistent_provider_xyz") is None

    def test_finds_vllm_local(self):
        spec = find_by_name("vllm")
        assert spec is not None
        assert spec.is_local

    def test_finds_minimax(self):
        spec = find_by_name("minimax")
        assert spec is not None


class TestFindGateway:
    def test_detects_openrouter_by_key_prefix(self):
        spec = find_gateway(api_key="sk-or-test123")
        assert spec is not None
        assert spec.name == "openrouter"

    def test_detects_aihubmix_by_api_base(self):
        spec = find_gateway(api_base="https://aihubmix.com/v1")
        assert spec is not None
        assert spec.name == "aihubmix"

    def test_detects_siliconflow_by_api_base(self):
        spec = find_gateway(api_base="https://api.siliconflow.cn/v1")
        assert spec is not None
        assert spec.name == "siliconflow"

    def test_direct_provider_name(self):
        spec = find_gateway(provider_name="openrouter")
        assert spec is not None
        assert spec.name == "openrouter"

    def test_direct_local_name(self):
        spec = find_gateway(provider_name="vllm")
        assert spec is not None
        assert spec.is_local

    def test_returns_none_for_standard_provider(self):
        """Standard providers like anthropic should NOT be returned as gateways."""
        spec = find_gateway(provider_name="anthropic")
        assert spec is None

    def test_returns_none_when_no_match(self):
        spec = find_gateway(api_key="sk-normal-key", api_base="https://api.openai.com/v1")
        assert spec is None


class TestProvidersRegistry:
    def test_all_providers_have_name(self):
        for spec in PROVIDERS:
            assert spec.name, f"Provider missing name: {spec}"

    def test_all_providers_have_keywords(self):
        for spec in PROVIDERS:
            assert spec.keywords, f"Provider {spec.name} has no keywords"

    def test_no_duplicate_names(self):
        names = [s.name for s in PROVIDERS]
        assert len(names) == len(set(names)), "Duplicate provider names found"

    def test_opencode_is_direct(self):
        spec = find_by_name("opencode")
        assert spec is not None
        assert spec.is_direct

    def test_anthropic_supports_prompt_caching(self):
        spec = find_by_name("anthropic")
        assert spec.supports_prompt_caching

    def test_openrouter_supports_prompt_caching(self):
        spec = find_by_name("openrouter")
        assert spec.supports_prompt_caching

    def test_openai_codex_is_oauth(self):
        spec = find_by_name("openai_codex")
        assert spec.is_oauth

    def test_github_copilot_is_oauth(self):
        spec = find_by_name("github_copilot")
        assert spec.is_oauth

    def test_model_overrides_for_kimi(self):
        spec = find_by_name("moonshot")
        overrides = dict(spec.model_overrides)
        assert "kimi-k2.5" in overrides
        assert overrides["kimi-k2.5"]["temperature"] == 1.0
