does this codebase proactively do all of the below things?

- 🟢 run multiple agents in parallel in sandboxed environments (Docker/Subagent) in background and reports back the progress. it can create agents or we can ask it to.

- 🟢 communicates via slack and whatsapp
- 🟢 can send me proactive texts (Cron/MessageTool) on its own. doesnt need to be triggered by me.
- 🟢 is always online and always available (Gateway Service)
- 🟢 chat is never blocked by other processes (Asyncio Loop)
- 🟢 i can queue messages for it to do (MessageBus)

- 🟢 remembers everything and recalls relevant info before every response (MemoryStore + SOUL.md)

- 🟢 updates its own system prompt everynight to grow (nightly_soul_update)
- 🟢 observes and proactively improves itself (nightly_self_optimization) by creating new rules, skills, tools, policies and code to improve itself

- 🟢 creates new skills and write new tools for itself whenever it needs it (skill-creator + SpawnTool)

- 🟢 it itself runs in a sandboxed environment
- 🟢 runs all code in a sandboxed environment (Docker support in exec tool)
- 🟢 edits its own code if theres a bug, or some feature is missing (GOD MODE enabled)
- 🟢 parses ast to edit code (rewrite_code tool)
- 🟢 runs tests to test itself and any other code (pytest integration)
- 🟢 tracks its own upgrades in git and can rollback (/rollback)

- 🟢 logs everything that it does and monitors its own logs (loguru + agent oversight)

- 🟢 has a universal realtime webhook that catches all incoming events (Bus events)

- 🟢 has its own prioritization queue (MessageBus PriorityQueue)

- 🟢 all ai communication goes through opencode's default model

- 🟢 browses the web (using user profiles via browser_data_dir + Playwright MCP) and does deep web research
- 🟢 transcribes voice messages (Whisper)
- 🟢 supports multimodal inputs (Vision)

- 🟢 has its own task board (manage_tasks tool) in which i can add tasks and go to sleep to find it has done everything in the morning

- 🟢 has all permissions so that it can run without asking me any permissions
- 🟢 enforces a daily budget guardrail to prevent accidental high API costs

- 🟢 has a failsafe kill switch (/stop) that i can use to stop it
- 🟢 reloads itself instantly on code changes (ReloadTool) to apply updates
- 🟢 connect to external tools and sensors via Model Context Protocol (MCP)
- 🟢 provides a rich interactive CLI with persistent command history and markdown
- 🟢 summarizes daily git activity and reports highlights every evening
- 🟢 monitors a HEARTBEAT.md file for autonomous task execution without user input