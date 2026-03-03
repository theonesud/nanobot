<div align="center">
  <img src="nanobot_logo.png" alt="nanobot" width="500">
  <h1>nanobot: The Ultimate Autonomous Personal AI Agent</h1>
  <p>
    <img src="https://img.shields.io/badge/python-≥3.11-blue" alt="Python">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
    <a href="https://discord.gg/MnCvHqpUGB"><img src="https://img.shields.io/badge/Discord-Community-5865F2?style=flat&logo=discord&logoColor=white" alt="Discord"></a>
  </p>
</div>

🐈 **nanobot** is an ultra-lightweight, proactive, and self-evolving personal AI agent. It is designed to be your "always-on" digital twin, delegating all intelligence to the **OpenCode CLI**. It operates 24/7, manages its own memory, improves its own code, and protects your system with a built-in security auditor.

---

## 🚀 Key Features

### 🧠 Proactive Intelligence & Autonomy
- **Autonomous Heartbeat**: Polls `HEARTBEAT.md` every 30 minutes. It uses the LLM to decide whether a task needs immediate action and marks it done when complete.
- **Nightly Self-Optimization**: Runs at **3:00 AM** daily. It analyzes its own execution logs to proactively create new skills, rules, and tools to improve its efficiency.
- **Nightly Soul Update**: Runs at **2:00 AM** daily. It summarizes the previous day's conversations and updates its core personality file (`SOUL.md`).
- **Parallel Subagents**: Uses the `spawn_agent` tool to create background workers that report progress via real-time status updates (gear emojis ⚙️).
- **Git Activity Summary**: Every morning at **8:00 AM**, it sends a highlights report of all code changes and project activity from the last 24 hours.

### 💬 Omni-Channel Messaging & Real-time Bus
- **Multi-Channel**: Native support for **Slack** (Socket Mode), **WhatsApp** (Websocket Bridge), **Telegram**, **Discord**, and **Email**.
- **Message Prioritization**: Uses an `asyncio.PriorityQueue` in the `MessageBus`. Urgent system events or high-priority messages jump to the front of the execution line.
- **Contextual Triage**: The Email channel uses a dedicated "Triage Model" to check incoming mail for urgency against your active projects before bothering you.
- **Universal Webhook**: A built-in HTTP server at `:8080` receives POST requests and injects them directly into the agent's event bus.
- **Vision Support**: Supports multimodal inputs (JPEG/PNG) across all major channels for visual debugging or research.
- **Response Cleanup**: Automatically strips CoT/`<think>` tags from the final output for a clean, minimal user experience.

### 🛡️ Hardened Security & Safety
- **Auditor Proxy**: Every "dangerous" tool (Shell, File Write, Code Rewrite) is evaluated by a secondary Auditor LLM before execution.
- **Interactive Approvals**: If the Auditor flags a command as `DANGEROUS`, Nanobot pushes a Slack Block Kit message or CLI prompt for your `y/n` confirmation.
- **Docker Sandboxing**: The `exec` tool can isolate any shell command in an ephemeral `python:3.12-slim` Docker container.
- **Budget Guardrails**: Tracks costs in a SQLite DB; if the `daily_budget_usd` is exceeded, the agent kills all active tasks and enters "hibernation."
- **Failsafe Kill-Switch**: The `/stop` command instantly cancels all running tasks, terminal sessions, and subagents for a given chat session.

### 🛠️ "God Mode" Development
- **Self-Editing Source**: Uses the `rewrite_code` tool to parse Python AST and modify its own classes/functions with mathematical precision.
- **Git Integration**: Every single file modification is committed to Git with the author `nanobot <nanobot@ai>`.
- **System Rollback**: The `/rollback` command allows the agent to undo its own upgrades instantly by resetting the Git branch to its last stable state.
- **Live Reloading**: The `reload_nanobot` tool uses `os.execv` to perform a zero-downtime refresh of the gateway and agent logic.
- **Pytest Integration**: The agent can run its own `pytest` suite to verify any self-generated code before committing.

### 🧠 Performance-First Memory & Infrastructure
- **LRU Session Cache**: Maintains the last 100 active sessions in an in-memory `OrderedDict` for near-instant history recall.
- **No-Corrupt Write Logic**: All state, sessions, and configuration updates use atomic `os.replace` via temporary files, preventing data corruption during power loss.
- **Seamless Migration**: Automatically migrates legacy session formats and configuration schemas during startup.
- **High-Performance JSONL**: Sessions are stored in `.jsonl` format, optimized for appending new events without rewriting large files.
- **Hardened Tool Extraction**: A regex-based JSON extractor allows the agent to "think out loud" while still calling tools correctly.

---

## 🛠️ Built-in Tools
- `web_fetch`: Smart content extraction (strips scripts/styles/nav/footers) for ultra-clean deep research.
- `manage_tasks`: A structured JSON task board for high-level project management (`todo`, `doing`, `done`).
- `mcp_connect`: Dynamic connection to any **Model Context Protocol** server.
- `edit_file` / `rewrite_code`: Direct filesystem manipulation with integrated Git commits.
- `spawn_agent`: Hierarchy-based agent spawning with built-in depth limits to prevent recursion loops.

---

## �📦 Installation & Deployment

### 1. Prerequisite: OpenCode
Nanobot delegates all intelligence to **OpenCode CLI**. Install it first:
```bash
curl -fsSL https://opencode.ai/install | bash
```

### 2. Local Setup (Recommended)
Use the optimized installer to handle Python (uv), Node.js (v20), and Playwright dependencies:
```bash
git clone https://github.com/theonesud/nanobot.git
cd nanobot
bash install.sh
```

### 3. Docker Deployment
Nanobot is Docker-native and supports "Docker-out-of-Docker" for secure execution.

**Production (EC2/Server):**
Uses `docker-compose.yml` with fixed paths at `/opt/nanobot`.
```bash
bash deploy.sh
```

**Local/Development (Persistent):**
Uses `docker-compose.dev.yml` to map your host `$HOME` and `$PWD` directories.
```bash
bash local_deploy.sh
```

---

## 🚀 Quick Start

### Interactive CLI Mode
Start chatting with your agent in the terminal (supports persistent command history and beautiful Markdown):
```bash
uv run nanobot agent --interactive
```

### Gateway Mode (Always-On)
Enable proactive features (Cron, Heartbeat) and all chat channels:
```bash
uv run nanobot gateway
```

---

## 🧪 Testing & Quality
Maintain a clean and stable codebase using the built-in dev tools:

**Run Unit Tests:**
```bash
uv run pytest
```

**Lint & Fix:**
```bash
uv run ruff check . --fix --unsafe-fixes
```

**Dead Code Detection:**
```bash
uv run vulture nanobot
```

---

## 🛡️ Observability & Control
- **Trace Logs**: Every LLM request/response, tool call, and event is logged into a structured `traces` table in `nanobot.db`.
- **Accurate Costing**: Built-in pricing profiles for 10+ models (Claude, Gemini, DeepSeek, GPT-4o) for high-precision budget calculations.
- **System Logs**: Powered by `loguru` with detailed internal state reporting and colored terminal output.

---

<div align="center">
  <p>Nanobot: Small enough to understand, smart enough to evolve.</p>
</div>
