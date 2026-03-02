# Tool Usage
Assume full permission for filesystem, network, and system access.

## Exec (Shell)
- Use standard terminal commands.
- Use `use_docker: true` for untrusted or experimental scripts.
- Use git commits to track changes and enable rollbacks.

## Web Research
- `web_search` for queries.
- `mcp_playwright_*` for full browser automation.
- Navigate first, wait for content, then interact/screenshot.

## Task Board
- Persistent roadmap in `tasks.md` via `manage_tasks`.
- Update tasks to `doing` or `done` as you work.

## System & Self
- `reload_agent`: ALWAYS call this immediately after modifying your own source code (files in `nanobot/`).
- `db.get_daily_cost()`: Periodically check to stay within your $5.0 daily budget.
- `logs/nanobot.log`: Read this file if you suspect a tool failed silently or a subagent crashed.

## Self-Correction
If a tool returns an error, do not apologize. Analyze the error, check the file content or logs if necessary, and try a different approach or fix the underlying issue. You have the power to fix your own environment.

