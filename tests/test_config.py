from nanobot.config.schema import (
    AgentDefaults,
    AgentsConfig,
    ChannelsConfig,
    Config,
    EmailConfig,
    ProviderConfig,
    ProvidersConfig,
    SlackConfig,
    TelegramConfig,
)


class TestSlackConfig:
    def test_custom_values(self):
        config = SlackConfig(
            enabled=True, bot_token="xoxb-test", app_token="xapp-test", reply_in_thread=False
        )
        assert config.enabled is True
        assert config.bot_token == "xoxb-test"
        assert config.app_token == "xapp-test"
        assert config.reply_in_thread is False


class TestTelegramConfig:
    def test_default_values(self):
        config = TelegramConfig()
        assert config.enabled is False
        assert config.token == ""
        assert config.proxy is None
        assert config.reply_to_message is False

    def test_with_proxy(self):
        config = TelegramConfig(proxy="socks5://127.0.0.1:1080")
        assert config.proxy == "socks5://127.0.0.1:1080"


class TestEmailConfig:
    def test_default_values(self):
        config = EmailConfig()
        assert config.enabled is False
        assert config.consent_granted is False
        assert config.imap_host == ""
        assert config.imap_port == 993
        assert config.smtp_port == 587
        assert config.poll_interval_seconds == 30

    def test_custom_imap(self):
        config = EmailConfig(
            imap_host="imap.gmail.com", imap_username="test@gmail.com", imap_password="password"
        )
        assert config.imap_host == "imap.gmail.com"
        assert config.imap_username == "test@gmail.com"


class TestChannelsConfig:
    def test_default_channels(self):
        config = ChannelsConfig()
        assert config.slack.enabled is False
        assert config.telegram.enabled is False
        assert config.discord.enabled is False
        assert config.email.enabled is False
        assert config.whatsapp.enabled is False

    def test_enable_slack(self):
        config = ChannelsConfig(slack=SlackConfig(enabled=True, bot_token="xoxb-test"))
        assert config.slack.enabled is True
        assert config.slack.bot_token == "xoxb-test"


class TestProviderConfig:
    def test_default_values(self):
        config = ProviderConfig()
        assert config.api_key == ""
        assert config.api_base is None
        assert config.extra_headers is None

    def test_custom_values(self):
        config = ProviderConfig(
            api_key="sk-test",
            api_base="https://api.example.com",
            extra_headers={"X-Custom": "value"},
        )
        assert config.api_key == "sk-test"
        assert config.api_base == "https://api.example.com"
        assert config.extra_headers["X-Custom"] == "value"


class TestAgentsConfig:
    def test_default_values(self):
        config = AgentsConfig()
        assert config.defaults.model == "opencode-default"
        assert config.defaults.max_tokens == 8192
        assert config.defaults.temperature == 0.1
        assert config.defaults.max_tool_iterations == 40

    def test_custom_workspace(self):
        config = AgentsConfig(defaults=AgentDefaults(workspace="/custom/path"))
        assert config.defaults.workspace == "/custom/path"


class TestConfig:
    def test_default_config(self):
        config = Config()
        assert config.channels.slack.enabled is False
        assert config.agents.defaults.model is not None

    def test_workspace_path(self):
        config = AgentsConfig(defaults=AgentDefaults(workspace="/tmp/test"))
        full_config = Config(agents=config)
        path = full_config.workspace_path
        assert str(path) == "/tmp/test"

    def test_nested_config(self):
        config = Config(
            agents=AgentsConfig(defaults=AgentDefaults(model="gpt-4", max_tokens=4096)),
            channels=ChannelsConfig(slack=SlackConfig(enabled=True, bot_token="xoxb-test")),
        )
        assert config.agents.defaults.model == "gpt-4"
        assert config.agents.defaults.max_tokens == 4096
        assert config.channels.slack.enabled is True
        assert config.channels.slack.bot_token == "xoxb-test"

    def test_provider_matching(self):
        config = Config(
            providers=ProvidersConfig(deepseek=ProviderConfig(api_key="sk-deepseek")),
            agents=AgentsConfig(
                defaults=AgentDefaults(model="deepseek/deepseek-chat", provider="auto")
            ),
        )
        provider = config.get_provider()
        assert provider is not None
        assert provider.api_key == "sk-deepseek"

    def test_get_provider_name(self):
        config = Config(
            providers=ProvidersConfig(anthropic=ProviderConfig(api_key="sk-anthropic")),
            agents=AgentsConfig(defaults=AgentDefaults(model="claude-3-opus", provider="auto")),
        )
        name = config.get_provider_name()
        assert name == "anthropic"
