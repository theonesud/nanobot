import time
from unittest.mock import AsyncMock

import pytest

from nanobot.cron.service import CronService, _compute_next_run, _validate_schedule_for_add
from nanobot.cron.types import CronSchedule


class TestComputeNextRun:
    def test_at_future(self):
        future_ms = int(time.time() * 1000) + 60000
        sched = CronSchedule(kind="at", at_ms=future_ms)
        result = _compute_next_run(sched, int(time.time() * 1000))
        assert result == future_ms

    def test_at_past_returns_none(self):
        past_ms = int(time.time() * 1000) - 60000
        sched = CronSchedule(kind="at", at_ms=past_ms)
        result = _compute_next_run(sched, int(time.time() * 1000))
        assert result is None

    def test_every_ms(self):
        now_ms = int(time.time() * 1000)
        sched = CronSchedule(kind="every", every_ms=5000)
        result = _compute_next_run(sched, now_ms)
        assert result == now_ms + 5000

    def test_every_ms_zero_returns_none(self):
        sched = CronSchedule(kind="every", every_ms=0)
        result = _compute_next_run(sched, int(time.time() * 1000))
        assert result is None

    def test_cron_expression(self):
        now_ms = int(time.time() * 1000)
        sched = CronSchedule(kind="cron", expr="* * * * *")
        result = _compute_next_run(sched, now_ms)
        assert result is not None
        assert result > now_ms

    def test_unknown_kind_returns_none(self):
        sched = CronSchedule(kind="unknown")
        result = _compute_next_run(sched, 0)
        assert result is None


class TestValidateScheduleForAdd:
    def test_valid_cron(self):
        sched = CronSchedule(kind="cron", expr="0 * * * *")
        _validate_schedule_for_add(sched)

    def test_tz_on_non_cron_raises(self):
        sched = CronSchedule(kind="every", every_ms=5000, tz="UTC")
        with pytest.raises(ValueError, match="tz can only be used with cron"):
            _validate_schedule_for_add(sched)

    def test_invalid_tz_raises(self):
        sched = CronSchedule(kind="cron", expr="0 * * * *", tz="Invalid/Timezone")
        with pytest.raises(ValueError, match="unknown timezone"):
            _validate_schedule_for_add(sched)

    def test_valid_tz_on_cron(self):
        sched = CronSchedule(kind="cron", expr="0 * * * *", tz="UTC")
        _validate_schedule_for_add(sched)


class TestCronServicePublicAPI:
    @pytest.fixture
    def service(self, tmp_path):
        store_path = tmp_path / "cron.json"
        return CronService(store_path=store_path, on_job=AsyncMock())

    @pytest.mark.asyncio
    async def test_add_job_returns_job_with_id(self, service):
        job = await service.add_job(
            name="test", schedule=CronSchedule(kind="cron", expr="0 * * * *"), message="ping"
        )
        assert job.id is not None
        assert len(job.id) > 0

    @pytest.mark.asyncio
    async def test_add_job_persists_to_disk(self, service, tmp_path):
        await service.add_job(
            name="persistent", schedule=CronSchedule(kind="cron", expr="0 * * * *"), message="ping"
        )
        store_path = tmp_path / "cron.json"
        assert store_path.exists()

    @pytest.mark.asyncio
    async def test_add_job_appears_in_list(self, service):
        job = await service.add_job(
            name="listed", schedule=CronSchedule(kind="cron", expr="0 * * * *"), message="ping"
        )
        jobs = service.list_jobs()
        assert any((j.id == job.id for j in jobs))

    @pytest.mark.asyncio
    async def test_list_jobs_excludes_disabled_by_default(self, service):
        job = await service.add_job(
            name="tog", schedule=CronSchedule(kind="cron", expr="0 * * * *"), message="ping"
        )
        await service.enable_job(job.id, enabled=False)
        jobs = service.list_jobs()
        assert not any((j.id == job.id for j in jobs))

    @pytest.mark.asyncio
    async def test_list_jobs_includes_disabled_when_flag_set(self, service):
        job = await service.add_job(
            name="tog2", schedule=CronSchedule(kind="cron", expr="0 * * * *"), message="ping"
        )
        await service.enable_job(job.id, enabled=False)
        jobs = service.list_jobs(include_disabled=True)
        assert any((j.id == job.id for j in jobs))

    @pytest.mark.asyncio
    async def test_remove_job_returns_true(self, service):
        job = await service.add_job(
            name="remove", schedule=CronSchedule(kind="cron", expr="0 * * * *"), message="ping"
        )
        removed = await service.remove_job(job.id)
        assert removed is True

    @pytest.mark.asyncio
    async def test_remove_job_returns_false_for_missing(self, service):
        removed = await service.remove_job("nonexistent-id-xyz")
        assert removed is False

    @pytest.mark.asyncio
    async def test_enable_job_sets_enabled(self, service):
        job = await service.add_job(
            name="enable", schedule=CronSchedule(kind="cron", expr="0 * * * *"), message="ping"
        )
        await service.enable_job(job.id, enabled=False)
        await service.enable_job(job.id, enabled=True)
        jobs = service.list_jobs()
        matching = [j for j in jobs if j.id == job.id]
        assert len(matching) == 1
        assert matching[0].enabled is True

    @pytest.mark.asyncio
    async def test_disable_job_clears_next_run(self, service):
        job = await service.add_job(
            name="disable", schedule=CronSchedule(kind="cron", expr="0 * * * *"), message="ping"
        )
        result = await service.enable_job(job.id, enabled=False)
        assert result is not None
        assert result.state.next_run_at_ms is None

    def test_status_returns_dict(self, service):
        status = service.status()
        assert "enabled" in status
        assert "jobs" in status
        assert "next_wake_at_ms" in status

    @pytest.mark.asyncio
    async def test_status_job_count(self, service):
        await service.add_job(
            name="count1", schedule=CronSchedule(kind="cron", expr="0 * * * *"), message="ping"
        )
        await service.add_job(
            name="count2", schedule=CronSchedule(kind="cron", expr="0 * * * *"), message="ping"
        )
        status = service.status()
        assert status["jobs"] == 2

    @pytest.mark.asyncio
    async def test_start_loads_and_runs(self, service):
        await service.add_job(
            name="start_test", schedule=CronSchedule(kind="cron", expr="0 * * * *"), message="ping"
        )
        await service.start()
        assert service._running is True
        service.stop()

    @pytest.mark.asyncio
    async def test_stop_sets_running_false(self, service):
        await service.start()
        service.stop()
        assert service._running is False

    @pytest.mark.asyncio
    async def test_add_job_with_deliver_and_channel(self, service):
        job = await service.add_job(
            name="deliver",
            schedule=CronSchedule(kind="cron", expr="0 * * * *"),
            message="report",
            deliver=True,
            channel="slack",
            to="C123",
        )
        assert job.payload.deliver is True
        assert job.payload.channel == "slack"
        assert job.payload.to == "C123"

    @pytest.mark.asyncio
    async def test_add_job_delete_after_run(self, service):
        job = await service.add_job(
            name="once",
            schedule=CronSchedule(kind="at", at_ms=9999999999999),
            message="run once",
            delete_after_run=True,
        )
        assert job.delete_after_run is True

    @pytest.mark.asyncio
    async def test_run_job_executes_callback(self, tmp_path):
        callback = AsyncMock()
        service = CronService(store_path=tmp_path / "cron.json", on_job=callback)
        job = await service.add_job(
            name="run_me", schedule=CronSchedule(kind="cron", expr="0 * * * *"), message="ping"
        )
        result = await service.run_job(job.id)
        assert result is True
        callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_disabled_job_blocked_without_force(self, service):
        job = await service.add_job(
            name="disabled_run",
            schedule=CronSchedule(kind="cron", expr="0 * * * *"),
            message="ping",
        )
        await service.enable_job(job.id, enabled=False)
        result = await service.run_job(job.id)
        assert result is False

    @pytest.mark.asyncio
    async def test_run_disabled_job_forced(self, service):
        job = await service.add_job(
            name="force_run", schedule=CronSchedule(kind="cron", expr="0 * * * *"), message="ping"
        )
        await service.enable_job(job.id, enabled=False)
        result = await service.run_job(job.id, force=True)
        assert result is True

    @pytest.mark.asyncio
    async def test_persistence_reload(self, tmp_path):
        store = tmp_path / "cron.json"
        s1 = CronService(store_path=store)
        j = await s1.add_job(
            name="reload", schedule=CronSchedule(kind="cron", expr="0 * * * *"), message="hi"
        )
        job_id = j.id
        s2 = CronService(store_path=store)
        jobs = s2.list_jobs()
        assert any((j.id == job_id for j in jobs))
