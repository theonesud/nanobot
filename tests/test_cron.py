"""Tests for cron/service.py and cron/tasks.py."""

from unittest.mock import AsyncMock

import pytest

from nanobot.cron.service import CronService
from nanobot.cron.types import CronJob, CronSchedule


class TestCronSchedule:
    """Tests for CronSchedule."""

    def test_cron_schedule_parse(self):
        """Test cron expression parsing."""
        schedule = CronSchedule(kind="cron", expr="0 * * * *")  # Every hour
        assert schedule.expr == "0 * * * *"


class TestCronJob:
    """Tests for CronJob."""

    def test_cron_job_creation(self):
        """Test creating a cron job."""
        job = CronJob(
            id="test-task",
            name="Check something",
            schedule=CronSchedule(kind="cron", expr="0 * * * *"),
            enabled=True,
        )
        assert job.id == "test-task"
        assert job.schedule.expr == "0 * * * *"
        assert job.enabled is True


class TestCronService:
    """Tests for CronService."""

    @pytest.fixture
    def cron_service(self, temp_workspace):
        """Create a cron service instance."""
        store_path = temp_workspace / "cron.json"
        return CronService(store_path, AsyncMock())

    def test_service_initialization(self, cron_service, temp_workspace):
        """Test cron service initializes correctly."""
        assert cron_service.store_path is not None

    @pytest.mark.asyncio
    async def test_add_job(self, cron_service):
        """Test adding a cron job."""
        job = cron_service.add_job(
            name="Daily check", schedule=CronSchedule(kind="cron", expr="0 9 * * *"), message="ping"
        )
        assert getattr(job, "id", None) is not None
        jobs = cron_service.list_jobs()
        assert any(j.id == job.id for j in jobs)

    @pytest.mark.asyncio
    async def test_remove_job(self, cron_service):
        """Test removing a cron job."""
        job = cron_service.add_job(
            name="Test", schedule=CronSchedule(kind="cron", expr="0 * * * *"), message="test"
        )
        cron_service.remove_job(job.id)
        jobs = cron_service.list_jobs()
        assert not any(j.id == job.id for j in jobs)

    @pytest.mark.asyncio
    async def test_enable_disable_job(self, cron_service):
        """Test enabling and disabling a job."""
        job = cron_service.add_job(
            name="Test", schedule=CronSchedule(kind="cron", expr="0 * * * *"), message="test"
        )
        cron_service.enable_job(job.id, enabled=False)
        jobs = cron_service.list_jobs(include_disabled=True)
        assert any(j.id == job.id and not j.enabled for j in jobs)

        cron_service.enable_job(job.id, enabled=True)
        jobs = cron_service.list_jobs()
        assert any(j.id == job.id and j.enabled for j in jobs)

    @pytest.mark.asyncio
    async def test_list_jobs(self, cron_service):
        """Test listing all jobs."""
        cron_service.add_job(
            name="Test1", schedule=CronSchedule(kind="cron", expr="0 * * * *"), message="test"
        )
        cron_service.add_job(
            name="Test2", schedule=CronSchedule(kind="cron", expr="0 * * * *"), message="test"
        )

        jobs = cron_service.list_jobs()
        assert len(jobs) >= 2
