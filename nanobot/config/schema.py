from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from pydantic_settings import BaseSettings


class Base(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class WhatsAppConfig(Base):
    enabled: bool = True
    bridge_url: str = "ws://localhost:3001"
    bridge_token: str = ""
    allow_from: list[str] = Field(default_factory=list)


class TelegramConfig(Base):
    enabled: bool = False
    token: str = ""
    allow_from: list[str] = Field(default_factory=list)
    proxy: str | None = None
    reply_to_message: bool = False


class DiscordConfig(Base):
    enabled: bool = False
    token: str = ""
    allow_from: list[str] = Field(default_factory=list)
    gateway_url: str = "wss://gateway.discord.gg/?v=10&encoding=json"
    intents: int = 37377


class EmailConfig(Base):
    enabled: bool = False
    consent_granted: bool = False
    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""
    imap_mailbox: str = "INBOX"
    imap_use_ssl: bool = True
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    from_address: str = ""
    poll_interval_seconds: int = 30
    allow_from: list[str] = Field(default_factory=list)


class SlackDMConfig(Base):
    enabled: bool = True
    policy: str = "open"
    allow_from: list[str] = Field(default_factory=list)


class SlackConfig(Base):
    enabled: bool = False
    mode: str = "socket"
    bot_token: str = ""
    app_token: str = ""
    reply_in_thread: bool = True
    dm: SlackDMConfig = Field(default_factory=SlackDMConfig)


class ChannelsConfig(Base):
    send_progress: bool = True
    send_tool_hints: bool = True
    whatsapp: WhatsAppConfig = Field(default_factory=WhatsAppConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)
    slack: SlackConfig = Field(default_factory=SlackConfig)


class AgentDefaults(Base):
    workspace: str = "~/.nanobot/workspace"
    model: str = "opencode-default"
    provider: str = "opencode"
    auditor_model: str = "opencode-default"
    auditor_provider: str = "opencode"
    max_tokens: int = 8192
    temperature: float = 0.1
    max_tool_iterations: int = 40
    memory_window: int = 100


class AgentsConfig(Base):
    defaults: AgentDefaults = Field(default_factory=AgentDefaults)


class ProviderConfig(Base):
    api_key: str = ""
    api_base: str | None = None


class ProvidersConfig(Base):
    opencode: ProviderConfig = Field(default_factory=ProviderConfig)


class HeartbeatConfig(Base):
    enabled: bool = True
    interval_s: int = 1800


class GatewayConfig(Base):
    host: str = "0.0.0.0"
    port: int = 18790
    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)


class ExecToolConfig(Base):
    timeout: int = 60
    path_append: str = ""
    docker_image: str | None = "python:3.12-slim"
    use_docker: bool = True


class MCPServerConfig(Base):
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    tool_timeout: int = 30


class ToolsConfig(Base):
    web: Any = Field(default_factory=dict)
    exec: ExecToolConfig = Field(default_factory=ExecToolConfig)
    restrict_to_workspace: bool = False
    browser_data_dir: str | None = None
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)


class Config(BaseSettings):
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)

    @property
    def workspace_path(self) -> Path:
        return Path(self.agents.defaults.workspace).expanduser()

    def get_provider(self, model: str | None = None) -> ProviderConfig:
        return self.providers.opencode

    def get_provider_name(self, model: str | None = None) -> str:
        return "opencode"

    def get_api_base(self, model: str | None = None) -> str | None:
        return self.providers.opencode.api_base

    model_config = ConfigDict(env_prefix="NANOBOT_", env_nested_delimiter="__")
