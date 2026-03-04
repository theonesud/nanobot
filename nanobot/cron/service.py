import asyncio
import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Coroutine

from loguru import logger

from nanobot.cron.types import CronJob, CronJobState, CronPayload, CronSchedule, CronStore
from nanobot.utils.files import atomic_write as _atomic_write


def _now_ms() -> int:
    return int(time.time() * 1000)


def _compute_next_run(schedule: CronSchedule, now_ms: int) -> int | None:
    if schedule.kind == "at":
        return schedule.at_ms if schedule.at_ms and schedule.at_ms > now_ms else None
    if schedule.kind == "every":
        if not schedule.every_ms or schedule.every_ms <= 0:
            return None
        return now_ms + schedule.every_ms
    if schedule.kind == "cron" and schedule.expr:
        try:
            from zoneinfo import ZoneInfo

            from croniter import croniter

            base_time = now_ms / 1000
            tz = ZoneInfo(schedule.tz) if schedule.tz else datetime.now().astimezone().tzinfo
            base_dt = datetime.fromtimestamp(base_time, tz=tz)
            cron = croniter(schedule.expr, base_dt)
            next_dt = cron.get_next(datetime)
            return int(next_dt.timestamp() * 1000)
        except Exception:
            return None
    return None


def _validate_schedule_for_add(schedule: CronSchedule) -> None:
    if schedule.tz and schedule.kind != "cron":
        raise ValueError("tz can only be used with cron schedules")
    if schedule.kind == "cron" and schedule.tz:
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(schedule.tz)
        except Exception:
            raise ValueError(f"unknown timezone '{schedule.tz}'") from None


class CronService:
    def __init__(
        self,
        store_path: Path,
        on_job: Callable[[CronJob], Coroutine[Any, Any, str | None]] | None = None,
    ):
        self.store_path = store_path
        self.on_job = on_job
        self._store: CronStore | None = None
        self._timer_task: asyncio.Task | None = None
        self._running = False
        self._lock = asyncio.Lock()
        self._active_tasks: set[asyncio.Task] = set()
        self._running_job_ids: set[str] = set()

    def _load_store(self) -> CronStore:
        if self._store:
            return self._store
        if self.store_path.exists():
            try:
                data = json.loads(self.store_path.read_text(encoding="utf-8"))
                jobs = []
                for j in data.get("jobs", []):
                    jobs.append(
                        CronJob(
                            id=j["id"],
                            name=j["name"],
                            enabled=j.get("enabled", True),
                            schedule=CronSchedule(
                                kind=j["schedule"]["kind"],
                                at_ms=j["schedule"].get("atMs"),
                                every_ms=j["schedule"].get("everyMs"),
                                expr=j["schedule"].get("expr"),
                                tz=j["schedule"].get("tz"),
                            ),
                            payload=CronPayload(
                                kind=j["payload"].get("kind", "agent_turn"),
                                message=j["payload"].get("message", ""),
                                deliver=j["payload"].get("deliver", False),
                                channel=j["payload"].get("channel"),
                                to=j["payload"].get("to"),
                            ),
                            state=CronJobState(
                                next_run_at_ms=j.get("state", {}).get("nextRunAtMs"),
                                last_run_at_ms=j.get("state", {}).get("lastRunAtMs"),
                                last_status=j.get("state", {}).get("lastStatus"),
                                last_error=j.get("state", {}).get("lastError"),
                            ),
                            created_at_ms=j.get("createdAtMs", 0),
                            updated_at_ms=j.get("updatedAtMs", 0),
                            delete_after_run=j.get("deleteAfterRun", False),
                        )
                    )
                self._store = CronStore(jobs=jobs)
            except Exception as e:
                logger.warning("Failed to load cron store: {}", e)
                self._store = CronStore()
        else:
            self._store = CronStore()
        return self._store

    def _save_store(self) -> None:
        if not self._store:
            return
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": self._store.version,
            "jobs": [
                {
                    "id": j.id,
                    "name": j.name,
                    "enabled": j.enabled,
                    "schedule": {
                        "kind": j.schedule.kind,
                        "atMs": j.schedule.at_ms,
                        "everyMs": j.schedule.every_ms,
                        "expr": j.schedule.expr,
                        "tz": j.schedule.tz,
                    },
                    "payload": {
                        "kind": j.payload.kind,
                        "message": j.payload.message,
                        "deliver": j.payload.deliver,
                        "channel": j.payload.channel,
                        "to": j.payload.to,
                    },
                    "state": {
                        "nextRunAtMs": j.state.next_run_at_ms,
                        "lastRunAtMs": j.state.last_run_at_ms,
                        "lastStatus": j.state.last_status,
                        "lastError": j.state.last_error,
                    },
                    "createdAtMs": j.created_at_ms,
                    "updatedAtMs": j.updated_at_ms,
                    "deleteAfterRun": j.delete_after_run,
                }
                for j in self._store.jobs
            ],
        }
        _atomic_write(self.store_path, json.dumps(data, indent=2, ensure_ascii=False))

    async def start(self) -> None:
        async with self._lock:
            self._running = True
            self._load_store()
            self._recompute_next_runs()
            await asyncio.to_thread(self._save_store)
            self._arm_timer()
        logger.info(
            "Cron service started with {} jobs", len(self._store.jobs if self._store else [])
        )

    async def stop(self) -> None:
        self._running = False
        if self._timer_task:
            self._timer_task.cancel()
            self._timer_task = None
        for t in self._active_tasks:
            t.cancel()
        if self._active_tasks:
            await asyncio.gather(*self._active_tasks, return_exceptions=True)
        self._active_tasks.clear()

    def _recompute_next_runs(self) -> None:
        if not self._store:
            return
        now = _now_ms()
        for job in self._store.jobs:
            if not job.enabled:
                continue
            if job.state.next_run_at_ms and job.state.next_run_at_ms > now:
                continue
            nxt = _compute_next_run(job.schedule, now)
            if nxt is None and job.schedule.kind == "at":
                logger.warning("Cron job '{}' has past 'at' schedule, marking as missed", job.name)
                job.state.last_status = "skipped"
            job.state.next_run_at_ms = nxt

    def _get_next_wake_ms(self) -> int | None:
        if not self._store:
            return None
        times = [
            j.state.next_run_at_ms for j in self._store.jobs if j.enabled and j.state.next_run_at_ms
        ]
        return min(times) if times else None

    def _arm_timer(self) -> None:
        if self._timer_task:
            self._timer_task.cancel()
        next_wake = self._get_next_wake_ms()
        if not next_wake or not self._running:
            return

        async def tick():
            current_next_wake = self._get_next_wake_ms()
            if current_next_wake:
                delay_ms = max(0, current_next_wake - _now_ms())
                logger.debug("⏰ Cron ticker waiting for {:.2f}s", delay_ms / 1000)
                await asyncio.sleep(delay_ms / 1000)
            else:
                await asyncio.sleep(60)
            if self._running:
                await self._on_timer()

        self._timer_task = asyncio.create_task(tick())

    async def _on_timer(self) -> None:
        if not self._store:
            return
        now = _now_ms()
        async with self._lock:
            due_jobs = [
                j
                for j in self._store.jobs
                if j.enabled
                and j.id not in self._running_job_ids
                and j.state.next_run_at_ms
                and (now >= j.state.next_run_at_ms)
            ]
        if due_jobs:
            logger.info("⏰ Cron: found {} due job(s)", len(due_jobs))
        else:
            logger.debug("⏰ Cron ticker woke up, no jobs due")

        async def _run_and_save(job: CronJob) -> None:
            try:
                await self._execute_job(job)
            except Exception:
                logger.exception("Cron job '{}' crashed", job.name)
            finally:
                async with self._lock:
                    self._running_job_ids.discard(job.id)
                    await asyncio.to_thread(self._save_store)
                self._arm_timer()

        for job in due_jobs:
            self._running_job_ids.add(job.id)
            t = asyncio.create_task(_run_and_save(job))
            self._active_tasks.add(t)
            t.add_done_callback(self._active_tasks.discard)
        if not due_jobs:
            self._arm_timer()

    async def _execute_job(self, job: CronJob) -> None:
        start_ms = _now_ms()
        logger.info("Cron: executing job '{}' ({})", job.name, job.id)
        result_status = "ok"
        result_error = None
        try:
            if self.on_job:
                await self.on_job(job)
            logger.info("Cron: job '{}' completed", job.name)
        except Exception as e:
            result_status = "error"
            result_error = str(e)
            logger.error("Cron: job '{}' failed: {}", job.name, e)

        async with self._lock:
            job.state.last_status = result_status
            job.state.last_error = result_error
            job.state.last_run_at_ms = start_ms
            job.updated_at_ms = _now_ms()
            if job.schedule.kind == "at":
                if job.delete_after_run:
                    self._store.jobs = [j for j in self._store.jobs if j.id != job.id]
                else:
                    job.enabled = False
                    job.state.next_run_at_ms = None
            else:
                job.state.next_run_at_ms = _compute_next_run(job.schedule, _now_ms())

    def list_jobs(self, include_disabled: bool = False) -> list[CronJob]:
        store = self._load_store()
        jobs = store.jobs if include_disabled else [j for j in store.jobs if j.enabled]
        return sorted(jobs, key=lambda j: j.state.next_run_at_ms or float("inf"))

    async def add_job(
        self,
        name: str,
        schedule: CronSchedule,
        message: str,
        deliver: bool = False,
        channel: str | None = None,
        to: str | None = None,
        delete_after_run: bool = False,
        kind: str = "agent_turn",
    ) -> CronJob:
        store = self._load_store()
        _validate_schedule_for_add(schedule)
        now = _now_ms()
        job = CronJob(
            id=uuid.uuid4().hex[:12],
            name=name,
            enabled=True,
            schedule=schedule,
            payload=CronPayload(
                kind=kind, message=message, deliver=deliver, channel=channel, to=to
            ),
            state=CronJobState(next_run_at_ms=_compute_next_run(schedule, now)),
            created_at_ms=now,
            updated_at_ms=now,
            delete_after_run=delete_after_run,
        )
        async with self._lock:
            store.jobs.append(job)
            await asyncio.to_thread(self._save_store)
            self._arm_timer()
        logger.info("Cron: added job '{}' ({})", name, job.id)
        return job

    def status(self) -> dict:
        store = self._load_store()
        return {
            "enabled": self._running,
            "jobs": len(store.jobs),
            "next_wake_at_ms": self._get_next_wake_ms(),
        }
