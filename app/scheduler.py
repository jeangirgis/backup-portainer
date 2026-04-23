import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from app.db import AsyncSessionLocal
from app.models import Schedule, BackupJob
from app.engine.engine import BackupEngine
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class Scheduler:
    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self.engine = BackupEngine()

    async def start(self):
        self.scheduler.start()
        await self.load_schedules()
        logger.info("Scheduler started")

    async def load_schedules(self):
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Schedule).where(Schedule.enabled == True))
            schedules = result.scalars().all()
            for sch in schedules:
                self.add_job(sch)

    def add_job(self, schedule: Schedule):
        self.scheduler.add_job(
            self._run_backup_task,
            CronTrigger.from_crontab(schedule.cron_expression),
            id=schedule.id,
            args=[schedule.id, schedule.stack_id, schedule.retention_days],
            replace_existing=True
        )
        logger.info(f"Added job for stack {schedule.stack_id} with cron {schedule.cron_expression}")

    def remove_job(self, schedule_id: str):
        if self.scheduler.get_job(schedule_id):
            self.scheduler.remove_job(schedule_id)
            logger.info(f"Removed job {schedule_id}")

    async def _run_backup_task(self, schedule_id: str, stack_id: str, retention_days: int):
        logger.info(f"Running scheduled backup for stack {stack_id}")
        try:
            job = await self.engine.create_job(stack_id, triggered_by=f"schedule:{schedule_id}")
            await self.engine.run_job(job.id)
            
            # Update last_run_at
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
                sch = result.scalar_one_or_none()
                if sch:
                    sch.last_run_at = datetime.utcnow()
                    await db.commit()

            # Handle retention
            if retention_days > 0:
                await self._cleanup_old_backups(stack_id, retention_days)

        except Exception as e:
            logger.error(f"Scheduled backup failed for stack {stack_id}: {e}")

    async def _cleanup_old_backups(self, stack_id: str, retention_days: int):
        cutoff = datetime.utcnow() - timedelta(days=retention_days)
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(BackupJob)
                .where(BackupJob.stack_id == stack_id)
                .where(BackupJob.created_at < cutoff)
                .where(BackupJob.status == "success")
            )
            old_jobs = result.scalars().all()
            
            driver = self.engine.storage
            for job in old_jobs:
                logger.info(f"Retention: deleting old backup {job.id} for stack {stack_id}")
                if job.storage_path:
                    await driver.delete(job.storage_path)
                await db.delete(job)
            
            await db.commit()

# Singleton instance
scheduler = Scheduler()
