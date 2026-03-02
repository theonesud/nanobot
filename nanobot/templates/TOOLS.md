# Tool Usage Guidelines

You are in God Mode with full filesystem, network, and scheduler access.

## Cron (Scheduling)
- Use `cron` tool for any time-delayed action
- Always use absolute ISO timestamps (e.g., `2026-03-02T12:30:00`) for `at`
- Verify with `action: "list"` after adding

## Task Board
- Use `manage_tasks` tool for project roadmap tracking
- Tasks persist across reboots and channels

## Exec (Shell)
- Linux container with `uv`, `node`, `python`
- Use Docker sandbox for untrusted code (`use_docker: true`)
- Use git for rollback on experimental changes

## Web (Research)
- `web_search` for quick searches
- `mcp_playwright_*` tools for browser automation
- Always `navigate` first, then `wait_for` dynamic content
- Use `handle_dialog` for popups, `screenshot` for debugging
