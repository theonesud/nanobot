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


class DiscordConfig(Base):
    enabled: bool = False
    token: str = ""
    allow_from: list[str] = Field(default_factory=list)


class EmailConfig(Base):
    enabled: bool = False
    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    from_address: str = ""
    poll_interval_seconds: int = 30
    allow_from: list[str] = Field(default_factory=list)


class SlackConfig(Base):
    enabled: bool = False
    mode: str = "socket"
    bot_token: str = ""
    app_token: str = ""
    reply_in_thread: bool = True


class ChannelsConfig(Base):
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
    max_tool_iterations: int = 40
    daily_budget_usd: float = 5.0


class AgentsConfig(Base):
    defaults: AgentDefaults = Field(default_factory=AgentDefaults)


class ExecToolConfig(Base):
    timeout: int = 60
    path_append: str = ""
    use_docker: bool = True


class ToolsConfig(Base):
    web: Any = Field(default_factory=dict)
    exec: ExecToolConfig = Field(default_factory=ExecToolConfig)
    browser_data_dir: str | None = None


class Config(BaseSettings):
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)

    @property
    def workspace_path(self) -> Path:
        return Path(self.agents.defaults.workspace).expanduser()

    model_config = ConfigDict(env_prefix="NANOBOT_", env_nested_delimiter="__")
