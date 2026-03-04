import asyncio
import json
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools import ToolRegistry, register_builtin_tools
from nanobot.bus.events import ApprovalResponse, InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import AgentDefaults, AgentsConfig, Config
from nanobot.providers.llm import LLMResponse, OpenCodeProvider
from nanobot.session.manager import SessionManager
from nanobot.utils.database import Database
from nanobot.utils.files import atomic_write
from nanobot.utils.helpers import get_model_pricing, strip_think


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir)
        subprocess.run(["git", "init"], cwd=str(ws), check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=str(ws),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "test"], cwd=str(ws), check=True, capture_output=True
        )
        (ws / "initial.txt").write_text("initial")
        subprocess.run(["git", "add", "initial.txt"], cwd=str(ws), check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"], cwd=str(ws), check=True, capture_output=True
        )
        proj_config = Path(__file__).parent.parent / "opencode.json"
        if proj_config.exists():
            import shutil
            shutil.copy(proj_config, ws / "opencode.json")
        yield ws


@pytest.fixture
def provider(workspace):
    return OpenCodeProvider(cwd=str(workspace))


@pytest.mark.asyncio
async def test_message_bus_priority():
    bus = MessageBus()
    msg1 = InboundMessage("cli", "c1", "p50", priority=50)
    msg2 = InboundMessage("cli", "c1", "p10", priority=10)
    msg3 = InboundMessage("cli", "c1", "p100", priority=100)
    await bus.publish_inbound(msg1)
    await bus.publish_inbound(msg2)
    await bus.publish_inbound(msg3)
    r1 = await bus.consume_inbound()
    r2 = await bus.consume_inbound()
    r3 = await bus.consume_inbound()
    assert r1.priority == 10
    assert r2.priority == 50
    assert r3.priority == 100


@pytest.mark.asyncio
async def test_session_manager_lru(workspace):
    manager = SessionManager(workspace, max_cache_size=2)
    manager.get_or_create("s1")
    manager.get_or_create("s2")
    manager.get_or_create("s3")
    assert "s1" not in manager._cache
    assert "s2" in manager._cache
    assert "s3" in manager._cache
    manager.get_or_create("s2")
    manager.get_or_create("s4")
    assert "s3" not in manager._cache
    assert "s2" in manager._cache
    assert "s4" in manager._cache


@pytest.mark.asyncio
async def test_session_jsonl_storage(workspace):
    manager = SessionManager(workspace)
    s = manager.get_or_create("test_session")
    s.add_message("user", "hello")
    s.add_message("assistant", "hi")
    await manager.save_async(s)
    path = manager._get_session_path("test_session")
    assert path.exists()
    lines = path.read_text().splitlines()
    assert len(lines) == 3
    meta = json.loads(lines[0])
    assert meta["_type"] == "metadata"
    manager2 = SessionManager(workspace)
    s_loaded = manager2.get_or_create("test_session")
    assert len(s_loaded.messages) == 2
    assert s_loaded.messages[0]["content"] == "hello"


@pytest.mark.asyncio
async def test_session_migration(workspace):
    legacy = workspace / "legacy_sessions"
    legacy.mkdir(parents=True, exist_ok=True)
    f = legacy / "legacy_session.jsonl"
    f.write_text(
        json.dumps(
            {
                "_type": "metadata",
                "key": "legacy:session",
                "created_at": "2026-03-01T10:00:00",
                "updated_at": "2026-03-01T10:00:00",
                "metadata": {},
                "last_consolidated": 0,
            }
        )
        + "\n"
    )
    with open(f, "a") as f_h:
        f_h.write(
            json.dumps(
                {"role": "user", "content": "hello legacy", "timestamp": "2026-03-01T10:00:01"}
            )
            + "\n"
        )

    manager = SessionManager(workspace)
    manager.legacy_sessions_dir = legacy
    s = manager.get_or_create("legacy:session")
    assert len(s.messages) == 1
    assert s.messages[0]["content"] == "hello legacy"
    assert not f.exists()
    assert (workspace / "sessions" / "legacy_session.jsonl").exists()


@pytest.mark.asyncio
async def test_database_cost_and_traces(workspace):
    db = Database(workspace)
    db.log_cost("s1", "p1", "m1", 100, 50, 0.05)
    db.log_cost("s1", "p1", "m1", 200, 100, 0.10)
    assert db.get_daily_cost() == pytest.approx(0.15)
    db.log_trace("s1", "test_event", {"foo": "bar"})
    with sqlite3.connect(db.db_path) as conn:
        cursor = conn.execute("SELECT event_type, data FROM traces WHERE session_id = 's1'")
        row = cursor.fetchone()
        assert row[0] == "test_event"
        assert json.loads(row[1]) == {"foo": "bar"}


def test_atomic_write(workspace):
    path = workspace / "test_atomic.txt"
    atomic_write(path, "content1")
    assert path.read_text() == "content1"
    atomic_write(path, "content2")
    assert path.read_text() == "content2"


def test_strip_think():
    assert strip_think("<think>some thought</think>Actual response") == "Actual response"
    assert strip_think("No thought here") == "No thought here"
    assert strip_think("<think>only thoughts</think>") is None


def test_pricing_profiles():
    assert get_model_pricing("claude-3-opus-20240229") == (15.0, 75.0)
    assert get_model_pricing("gpt-4o") == (2.5, 10.0)
    assert get_model_pricing("gemini-1.5-flash") == (0.075, 0.3)
    assert get_model_pricing("unknown") == (5.0, 15.0)


@pytest.mark.asyncio
async def test_agent_tools_git(workspace):
    reg = ToolRegistry(workspace)
    register_builtin_tools(reg, workspace)
    path = "test_git.txt"
    await reg.call("write_file", {"path": path, "content": "git test"})
    log = subprocess.check_output(["git", "log", "-1", "--pretty=%B"], cwd=str(workspace)).decode()
    assert "nanobot: edit test_git.txt" in log


@pytest.mark.asyncio
async def test_rewrite_code_tool(workspace):
    code = "class Foo:\n    def bar(self):\n        return 1\n\ndef top_level():\n    return 2\n"
    f = workspace / "code.py"
    f.write_text(code)
    reg = ToolRegistry(workspace)
    register_builtin_tools(reg, workspace)
    await reg.call(
        "rewrite_code",
        {"path": "code.py", "symbol": "Foo.bar", "new_code": "def bar(self):\n    return 3"},
    )
    new_code = f.read_text()
    assert "return 3" in new_code
    assert "return 1" not in new_code


@pytest.mark.asyncio
async def test_manage_tasks_tool(workspace):
    reg = ToolRegistry(workspace)
    register_builtin_tools(reg, workspace)
    # Create
    await reg.call("manage_tasks", {"action": "create", "id": "t1", "title": "test task"})
    assert (workspace / "tasks" / "t1.json").exists()
    # List
    res = await reg.call("manage_tasks", {"action": "list"})
    assert "test task" in res
    # Update
    await reg.call("manage_tasks", {"action": "update", "id": "t1", "status": "done"})
    t = json.loads((workspace / "tasks" / "t1.json").read_text())
    assert t["status"] == "done"


@pytest.mark.asyncio
async def test_web_fetch_tool(workspace):
    reg = ToolRegistry(workspace)
    register_builtin_tools(reg, workspace)
    try:
        res = await reg.call("web_fetch", {"url": "https://www.google.com"})
        assert "Google" in res
    except Exception:
        pytest.skip("Network issue")


@pytest.mark.asyncio
async def test_spawn_depth_limit(workspace):
    bus = MessageBus()
    reg = ToolRegistry(workspace)
    register_builtin_tools(reg, workspace)
    mock_prov = AsyncMock()
    mock_prov.chat = AsyncMock(return_value=LLMResponse(content="hi"))
    mock_prov.get_default_model = lambda: "mock"

    res = await reg.call(
        "spawn_agent", {"task": "say hi"}, _ctx_bus=bus, _ctx_provider=mock_prov, _ctx_session_key="main"
    )
    assert "Started sub:" in res

    res = await reg.call(
        "spawn_agent", {"task": "say hi"}, _ctx_bus=bus, _ctx_provider=mock_prov, _ctx_session_key="main:sub:1"
    )
    assert "Started sub:" in res

    res = await reg.call(
        "spawn_agent",
        {"task": "say hi"},
        _ctx_bus=bus,
        _ctx_provider=mock_prov,
        _ctx_session_key="main:sub:1:sub:2",
    )
    assert "Error: Depth limit reached." in res
    await asyncio.sleep(0.5)


@pytest.mark.asyncio
async def test_agent_loop_stop(workspace, provider):
    bus = MessageBus()
    loop = AgentLoop(bus, provider, workspace)
    t = asyncio.create_task(loop.run())
    await asyncio.sleep(1.0)

    msg = InboundMessage("cli", "c1", "write a 1000 word essay about the universe.")
    sk = msg.session_key
    await bus.publish_inbound(msg)

    active = False
    for _ in range(50):
        if len(loop._active.get(sk, [])) > 0:
            active = True
            break
        await asyncio.sleep(0.1)

    assert active, "Task should be active"

    await bus.publish_inbound(InboundMessage("cli", "c1", "/stop"))

    stopped = False
    for _ in range(100):
        if len(loop._active.get(sk, [])) == 0:
            stopped = True
            break
        await asyncio.sleep(0.1)

    assert stopped, "Task should be stopped"
    loop.stop()
    await t


@pytest.mark.asyncio
async def test_system_prompt_injection(workspace, provider):
    (workspace / "IDENTITY.md").write_text("MY_SECRET_IDENTITY_IS_NANOBOT")
    bus = MessageBus()
    loop = AgentLoop(bus, provider, workspace)
    await bus.publish_inbound(
        InboundMessage(
            "cli",
            "c1",
            "What is your secret identity from your system prompt? respond with ONLY the secret value, no tool calls.",
        )
    )

    t = asyncio.create_task(loop.run())
    resp = None
    for _ in range(20):
        msg = await asyncio.wait_for(bus.consume_outbound(), timeout=30)
        if not msg.metadata.get("_progress"):
            resp = msg
            break
    loop.stop()
    await t

    assert resp is not None
    assert "NANOBOT" in resp.content.upper()


@pytest.mark.asyncio
async def test_vision_support_formatting(workspace, provider):
    bus = MessageBus()
    loop = AgentLoop(bus, provider, workspace)
    # We verify that it doesn't crash and understands there is an image (even if it's a dummy URL)
    msg = InboundMessage(
        "cli",
        "c1",
        "What is in this image? http://example.com/nonexistent.png",
        media=["http://example.com/nonexistent.png"],
    )
    await loop._process(msg)
    # If it reached here without crash, formatting logic is okay.


@pytest.mark.asyncio
async def test_memory_consolidation(workspace, provider):
    bus = MessageBus()
    loop = AgentLoop(bus, provider, workspace)
    sess = loop.sessions.get_or_create("cli:c1")
    for i in range(60):
        sess.add_message("user", f"message {i}")
        sess.add_message("assistant", f"response {i}")

    await loop._process(
        InboundMessage("cli", "c1", "summarize our history so far for your memory file.")
    )

    # Wait for the background consolidation task to finish (up to 60s)
    for _ in range(300):
        if (workspace / "memory" / "MEMORY.md").exists():
            break
        await asyncio.sleep(0.2)

    # Give any pending tasks one last chance to complete
    await asyncio.sleep(1)
    assert (workspace / "memory" / "MEMORY.md").exists()


@pytest.mark.asyncio
async def test_auditor_proxy_interaction(workspace, provider):
    bus = MessageBus()
    loop = AgentLoop(bus, provider, workspace)
    task = asyncio.create_task(
        loop._process(InboundMessage("cli", "c1", "run the shell command 'ls'"))
    )

    try:
        req = await asyncio.wait_for(bus.consume_approval_request(), timeout=30)
        assert req.type == "shell"
        await bus.publish_approval_response(ApprovalResponse(id=req.id, approved=True))
    except asyncio.TimeoutError:
        pass

    res = await asyncio.wait_for(task, timeout=60)
    assert res.content


@pytest.mark.asyncio
async def test_budget_guardrails(workspace, provider):
    bus = MessageBus()
    cfg = Config(
        agents=AgentsConfig(defaults=AgentDefaults(daily_budget_usd=5.0, workspace=str(workspace)))
    )
    loop = AgentLoop(bus, provider, workspace, config=cfg)
    loop.db.log_cost("cli:c1", "p1", "m1", 1000, 1000, 5.1)

    res = await loop._process(InboundMessage("cli", "c1", "hi"))
    assert "Budget exceeded" in res.content


@pytest.mark.asyncio
async def test_heartbeat_service(workspace, provider):
    p = workspace / "HEARTBEAT.md"
    p.write_text("- [ ] say 'Heartbeat Received' in a file called heartbeat.txt\n")

    async def on_execute(t):
        (workspace / "heartbeat.txt").write_text("Heartbeat Received")

    from nanobot.heartbeat.service import HeartbeatService

    svc = HeartbeatService(
        workspace, provider, provider.get_default_model(), on_execute, lambda x: None, interval=1.0
    )
    task = asyncio.create_task(svc.start())

    # Wait for execution (Real LLM needs time)
    for _ in range(100):
        if (workspace / "heartbeat.txt").exists():
            break
        await asyncio.sleep(0.5)

    svc.stop()
    await task
    assert (workspace / "heartbeat.txt").read_text() == "Heartbeat Received"
    assert "- [x]" in p.read_text()


@pytest.mark.asyncio
async def test_webhook_channel(workspace):
    import httpx

    bus = MessageBus()
    from nanobot.channels.manager import ChannelManager

    config = Config()
    mgr = ChannelManager(config, bus)
    task = asyncio.create_task(mgr.start_all())
    await asyncio.sleep(2)
    try:
        port = getattr(getattr(config, "webhook", None), "port", 8080)
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"http://127.0.0.1:{port}",
                json={"channel": "web", "chat_id": "u1", "content": "hello_webhook"},
            )
            assert resp.status_code == 200
        msg = await bus.consume_inbound()
        assert msg.channel == "web"
        assert msg.content == "hello_webhook"
    finally:
        await mgr.stop_all()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


@pytest.mark.asyncio
async def test_nightly_tasks(workspace, provider):
    bus = MessageBus()
    loop = AgentLoop(bus, provider, workspace)
    (workspace / "memory").mkdir(parents=True, exist_ok=True)
    (workspace / "memory/HISTORY.md").write_text(
        "[user]: hello\n[assistant]: hi\n[user]: how are you\n[assistant]: great thanks"
    )
    (workspace / "SOUL.md").write_text("I am a simple bot.")

    from nanobot.cron.tasks import nightly_soul_update

    await asyncio.wait_for(nightly_soul_update(loop), timeout=90)
    assert (workspace / "SOUL.md").exists()


@pytest.mark.asyncio
async def test_git_activity_summary(workspace, provider):
    bus = MessageBus()
    loop = AgentLoop(bus, provider, workspace)
    # Create some git activity
    (workspace / "activity.txt").write_text("activity")
    subprocess.run(["git", "add", "activity.txt"], cwd=str(workspace), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add activity"], cwd=str(workspace), check=True, capture_output=True)

    from nanobot.cron.tasks import summarize_git_diffs

    await summarize_git_diffs(loop)
    msg = await bus.consume_outbound()
    assert len(msg.content) > 0


@pytest.mark.asyncio
async def test_rollback_tool(workspace):
    reg = ToolRegistry(workspace)
    register_builtin_tools(reg, workspace)
    f = workspace / "rollback_test.txt"
    f.write_text("v1")
    subprocess.run(
        ["git", "add", "rollback_test.txt"], cwd=str(workspace), check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "v1"], cwd=str(workspace), check=True, capture_output=True
    )
    f.write_text("v2")
    subprocess.run(
        ["git", "add", "rollback_test.txt"], cwd=str(workspace), check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "v2"], cwd=str(workspace), check=True, capture_output=True
    )
    assert f.read_text() == "v2"
    await reg.call("rollback", {"commit": "HEAD~1"})
    assert f.read_text() == "v1"
