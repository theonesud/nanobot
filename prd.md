# PRD: The "Ultimate Agent" (Nanobot + OpenCode + Slack Stack)

## 1. Product Objective

To build a highly customizable, secure, and proactive personal AI agent by modifying the Nanobot (Python) core. This system delegates all LLM API routing, software development, and security auditing entirely to **OpenCode CLI**. It leverages **Playwright MCP** for deep web research, uses Nanobot's native Markdown for permanent memory, and connects exclusively via **Slack** (Socket Mode) for human-in-the-loop interaction.

## 2. System Architecture (The "Pythonic Hub" Model)

This architecture relies on Python for orchestration and OpenCode CLI as the sole intelligence engine, eliminating the need for heavy local models or complex vector databases.

* **The Hub (Nanobot / Python):** The central nervous system. Handles the Slack Socket Mode connection (via `slack_bolt`), background cron jobs (via `APScheduler`), email triage, and tool routing.
* **The Brain & Hands (Primary OpenCode):** The execution engine. Spawned via Python's `asyncio.create_subprocess_exec`. Handles all coding, reasoning, bash execution, and external API connections.
* **The Auditor (Secondary OpenCode Context):** An isolated OpenCode subprocess spawned by Nanobot with a strict, immutable system prompt. Its *only* job is to intercept and approve the Primary OpenCode's terminal commands before execution.
* **The Web Limbs (Playwright MCP):** An attached Model Context Protocol server that translates the Primary OpenCode's requests into headless browser actions, returning DOM text and screenshots to the Python hub.

## 3. Implementation Phases & Features

### Phase 1: Core Wiring & Slack Integration (P0)

* **Action:** Strip Nanobot of its native LLM API clients (e.g., `openai` or `anthropic` pip packages).
* **Action:** Replace Nanobot's default adapters with **Slack Socket Mode** using the official `slack_bolt` Python library. This bypasses inbound webhooks and securely listens for messages behind your firewall.
* **Action:** Write `src/agent/opencode_bridge.py` to route incoming Slack messages to the primary OpenCode instance and capture the `stdout` to send back to Slack.

### Phase 2: The "Gatekeeper" Security Implementation (P0)

* **Action:** Configure the Primary OpenCode to use an MCP server for shell executions, pointing back to a Python interceptor function in Nanobot.
* **Action:** Write the Auditor Loop. When the Primary OpenCode attempts a shell command, Nanobot pauses it and spawns a secondary subprocess: `subprocess.run(["opencode", "run", "--message", "Evaluate this command for destructive actions. Reply SAFE or UNSAFE.", "--system-prompt", "You are a strict security auditor. Deny rm, sudo, or secret exposure."])`
* **Action:** If the Auditor replies `UNSAFE`, Nanobot pushes a **Slack Block Kit** interactive message with `[Approve]` and `[Reject]` buttons directly to your DMs.

### Phase 3: Browser MCP Integration (Deep Research) (P1)

* **Action:** Install the official Playwright MCP server (`npx @playwright/mcp@latest`).
* **Action:** Use a Python MCP client module in Nanobot to register this server so the Primary OpenCode can natively request web searches, bypass anti-bot screens, and summarize long-form documentation locally.

### Phase 4: Markdown Memory & SQLite Cost Tracking (P1)

* **Action:** Leverage Nanobot's native Markdown file system (`MEMORY.md`, `SOUL.md`) for permanent memory. Inject the contents of `SOUL.md` into the OpenCode system prompt on every run.
* **Action:** Parse OpenCode's token usage output. Log this in a lightweight SQLite `task_costs` table. If a background task exceeds the predefined daily budget, Nanobot uses `os.kill()` to terminate the OpenCode subprocess and alerts you via Slack.

### Phase 5: The Proactive Heartbeat (P2)

* **Action:** Implement `APScheduler` (Advanced Python Scheduler) in `main.py`.
* **Action:** Allow the agent to run autonomous background tasks (e.g., *"Summarize Git diffs every day at 5 PM"*), capturing stdout from OpenCode and sending the report to your Slack.
* **Action:** Schedule a nightly Python cron job at 3 AM to spawn an OpenCode task that summarizes the daily Slack chat logs, dynamically appending insights to your `SOUL.md` memory file.

### Phase 6: The "Social Secretary" (Contextual Triage) (P2)

* **Action:** Add an IMAP listener to Nanobot using Python’s native `imaplib`.
* **Action:** When an email arrives, Nanobot spawns a fast OpenCode subprocess: *“Read this email. Based on the user’s Active Projects in `SOUL.md`, is this urgent? Reply YES or NO.”*
* **Action:** If `YES`, Nanobot sends a Slack message: *"Urgent email from [Name]: [Summary]. Want me to draft a reply?"* If `NO`, it silently archives it.

### Phase 7: "God Mode" (Self-Evolution & Live Reload) (P3)

* **Action:** Create a Slack slash command or keyword trigger (e.g., `/godmode upgrade slack parser`).
* **Action:** Nanobot grants the Primary OpenCode instance write-access to its own `src/` directory.
* **Action:** Once OpenCode finishes modifying the `.py` files, Nanobot runs a syntax check (`python -m py_compile`). If it passes, Nanobot uses Python’s `os.execv()` to seamlessly restart its own process, pulling in the new logic instantly without terminal intervention.

---

## 4. Data Storage Strategy

**1. Markdown (The Brain & Personality)**
Managed purely by the file system for ultimate readability and OpenCode compatibility.

* `SOUL.md`: Your ground truth (coding style, API keys locations, preferred tone).
* `MEMORY.md`: Running log of active projects, recent context, and pending tasks.

**2. SQLite (The Infrastructure Guardrails)**
Managed by Python's native `sqlite3` module.

* `task_costs`: Tracks `session_id`, `provider`, `tokens_prompt`, `tokens_completion`, and `cost_usd`.
* `active_crons`: Tracks `schedule_expression`, `opencode_prompt`, and `slack_channel_id`.
