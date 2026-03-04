import asyncio

import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.markdown import Markdown

from nanobot import __logo__
from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import ApprovalResponse, InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.manager import ChannelManager
from nanobot.config.loader import get_data_dir, load_config
from nanobot.cron.service import CronService
from nanobot.cron.types import CronSchedule
from nanobot.heartbeat.service import HeartbeatService
from nanobot.providers.llm import OpenCodeProvider

app, console = typer.Typer(name="nanobot", no_args_is_help=True), Console()


def _make_provider(config, model=None):
    return OpenCodeProvider(
        bin_path="opencode", default_model=model or config.agents.defaults.model,
        cwd=str(config.workspace_path),
    )


async def _handle_approvals(bus):
    while True:
        req = await bus.consume_approval_request()
        console.print(f"\n[red]Approval Required:[/red] {req.title}\nArgs: {req.content}")
        ans = await asyncio.to_thread(input, "Approve? (y/n): ")
        res = ApprovalResponse(id=req.id, approved=(ans.lower() == "y"))
        await bus.publish_approval_response(res)


@app.command()
def agent(
    interactive: bool = typer.Option(True, "--interactive/--no-interactive"),
    model: str = typer.Option(None, "--model", "-m"),
):
    async def run():
        config = load_config()
        bus, provider = MessageBus(), _make_provider(config, model)
        loop = AgentLoop(
            bus, provider, config.workspace_path, b_dir=config.tools.browser_data_dir, config=config
        )
        asyncio.create_task(_handle_approvals(bus))
        if interactive:
            asyncio.create_task(loop.run())
            sess = PromptSession()
            while True:
                try:
                    with patch_stdout():
                        t = await sess.prompt_async(HTML("<b fg='ansiblue'>You:</b> "))
                    if t.lower() in {"exit", "quit"}:
                        break
                    await bus.publish_inbound(InboundMessage("cli", "direct", t))
                    while True:
                        m = await bus.consume_outbound()
                        if m.metadata.get("_progress"):
                            console.print(f"[dim]{m.content}[/dim]")
                            continue
                        console.print(Markdown(m.content))
                        break
                except KeyboardInterrupt:
                    break
        else:
            await loop.run()

    asyncio.run(run())


@app.command()
def gateway():
    async def run():
        config = load_config()
        bus, provider = MessageBus(), _make_provider(config)
        cron = CronService(get_data_dir() / "cron/jobs.json")
        loop = AgentLoop(
            bus,
            provider,
            config.workspace_path,
            cron_service=cron,
            b_dir=config.tools.browser_data_dir,
            config=config,
        )
        channels = ChannelManager(config, bus, provider)
        heart = HeartbeatService(
            config.workspace_path,
            provider,
            config.agents.defaults.model,
            on_execute=lambda t: loop.process_direct(t, "heartbeat"),
            on_notify=lambda r: asyncio.create_task(
                bus.publish_outbound(OutboundMessage("cli", "direct", r))
            ),
            db=loop.db,
        )

        async def on_job(j):
            try:
                if j.payload.kind == "system_event":
                    from nanobot.cron import tasks

                    if fn := getattr(tasks, j.payload.message, None):
                        await fn(loop)
                else:
                    chan, to = j.payload.channel or "cli", j.payload.to or "direct"
                    res = await loop.process_direct(
                        j.payload.message, f"cron:{j.id}", channel=chan, chat_id=to
                    )
                    if res:
                        await bus.publish_outbound(OutboundMessage(chan, to, res))
            except Exception:
                from loguru import logger

                logger.exception("Cron job {} failed", j.id)

        cron.on_job = on_job
        for name, sched in {
            "nightly_soul_update": "0 2 * * *",
            "nightly_self_optimization": "0 3 * * *",
            "summarize_git_diffs": "0 8 * * *",
        }.items():
            if not any(j.name == f"System: {name}" for j in cron.list_jobs(include_disabled=True)):
                await cron.add_job(
                    f"System: {name}",
                    CronSchedule(kind="cron", expr=sched),
                    name,
                    kind="system_event",
                )

        shutdown_event = asyncio.Event()

        def _signal_handler():
            shutdown_event.set()

        aloop = asyncio.get_running_loop()
        import signal

        for sig in (signal.SIGINT, signal.SIGTERM):
            aloop.add_signal_handler(sig, _signal_handler)

        tasks = [
            asyncio.create_task(loop.run(), name="agent-loop"),
            asyncio.create_task(channels.start_all(), name="channels"),
            asyncio.create_task(cron.start(), name="cron"),
            asyncio.create_task(heart.start(), name="heartbeat"),
        ]
        done_waiter = asyncio.create_task(shutdown_event.wait())
        finished, _ = await asyncio.wait(
            [*tasks, done_waiter], return_when=asyncio.FIRST_COMPLETED
        )
        for t in tasks:
            t.cancel()
        heart.stop()
        await cron.stop()
        await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(run())


@app.command()
def status():
    config = load_config()
    console.print(
        f"{__logo__} nanobot status\nWorkspace: {config.workspace_path}\nProvider: OpenCode [green]✓[/green]"
    )


if __name__ == "__main__":
    app()
