import asyncio
import re
from datetime import datetime

from loguru import logger


class HeartbeatService:
    def __init__(self, workspace, provider, model, on_execute, on_notify, db=None, interval=1800):
        self.workspace, self.provider, self.model = workspace, provider, model
        self.on_execute, self.on_notify, self.interval, self.db = (
            on_execute,
            on_notify,
            interval,
            db,
        )
        self._running = False

    async def start(self):
        self._running = True
        while self._running:
            p = self.workspace / "HEARTBEAT.md"
            if p.exists():
                txt = p.read_text()
                # Find pending tasks: e.g. "- [ ] Task" or "- Task" (if not done)
                tasks = [m.group(1) for m in re.finditer(r"^- \[ \] (.*)", txt, re.M)]
                # Load memory context for better decision making
                mem_p = self.workspace / "memory" / "MEMORY.md"
                mem_txt = mem_p.read_text() if mem_p.exists() else "No recent memory."

                for t in tasks:
                    logger.info(f"❤️ Autonomous check: {t}")
                    # Decision logic: asking LLM if we should act NOW
                    prompt = f"System Heartbeat Audit.\nTask: {t}\nRecent Activity:\n{mem_txt}\n\nRespond with ACTION: <thinking> or PASS: <reason>."
                    msgs = [
                        {"role": "system", "content": "Heartbeat logic."},
                        {"role": "user", "content": prompt},
                    ]
                    if self.db:
                        self.db.log_trace(
                            "heartbeat",
                            "heartbeat_request",
                            {"model": self.model, "messages": msgs},
                        )
                    r = await self.provider.chat(msgs, model=self.model)
                    if self.db:
                        self.db.log_trace(
                            "heartbeat",
                            "heartbeat_response",
                            r.__dict__ if hasattr(r, "__dict__") else str(r),
                        )
                    if "ACTION" in r.content.upper():
                        # Execute and MARK AS DONE
                        await self.on_execute(t)
                        new_txt = txt.replace(
                            f"- [ ] {t}", f"- [x] {t} (Handled {datetime.now().date()})"
                        )
                        p.write_text(new_txt)
                        txt = new_txt
            await asyncio.sleep(self.interval)

    def stop(self):
        self._running = False
