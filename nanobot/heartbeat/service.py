import asyncio
import re
from datetime import datetime, timezone

from loguru import logger

_MAX_MEMORY_SIZE = 500_000


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
        self._stop_event = asyncio.Event()

    async def start(self):
        self._running = True
        while self._running:
            p = self.workspace / "HEARTBEAT.md"
            if p.exists():
                try:
                    txt = p.read_text()
                except OSError:
                    logger.debug("Failed to read HEARTBEAT.md")
                    txt = ""
                tasks = [m.group(1) for m in re.finditer(r"^- \[ \] (.*)", txt, re.M)]
                mem_p = self.workspace / "memory" / "MEMORY.md"
                mem_txt = "No recent memory."
                if mem_p.exists():
                    try:
                        raw = mem_p.read_text()
                        mem_txt = raw[-_MAX_MEMORY_SIZE:] if len(raw) > _MAX_MEMORY_SIZE else raw
                    except OSError:
                        pass

                for t in tasks:
                    try:
                        logger.info(f"❤️ Autonomous check: {t}")
                        prompt = f"System Heartbeat Audit.\nTask: {t}\nRecent Activity:\n{mem_txt}\n\nRespond ONLY with one of:\nACTION: <what to do>\nPASS: <reason>"
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
                        try:
                            r = await asyncio.wait_for(
                                self.provider.chat(msgs, model=self.model), timeout=120
                            )
                        except asyncio.TimeoutError:
                            logger.warning("Heartbeat LLM call timed out for task: {}", t)
                            continue
                        if self.db:
                            self.db.log_trace(
                                "heartbeat",
                                "heartbeat_response",
                                r.__dict__ if hasattr(r, "__dict__") else str(r),
                            )
                        content = (r.content or "").strip()
                        should_act = bool(re.match(r"^ACTION:", content, re.IGNORECASE))
                        if should_act:
                            await self.on_execute(t)
                            txt = p.read_text()
                            new_txt = txt.replace(
                                f"- [ ] {t}",
                                f"- [x] {t} (Handled {datetime.now(timezone.utc).date()})",
                            )
                            p.write_text(new_txt)
                    except Exception:
                        logger.exception("Heartbeat task '{}' failed", t)

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval)
                break
            except asyncio.TimeoutError:
                pass

    def stop(self):
        self._running = False
        self._stop_event.set()
