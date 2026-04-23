import tempfile
import shutil
import logging
from datetime import datetime
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import BackupJob
from app.engine.stack_exporter import StackExporter
from app.engine.volume_exporter import VolumeExporter
from app.engine.packager import Packager
from app.config import get_settings
from app.db import AsyncSessionLocal
from app.notifier import notifier

logger = logging.getLogger(__name__)

class BackupEngine:
    def __init__(self):
        self.settings = get_settings()
        self.stack_exporter = StackExporter(
            self.settings.PORTAINER_URL, 
            self.settings.PORTAINER_API_TOKEN,
            ssl_verify=self.settings.PORTAINER_SSL_VERIFY
        )
        self.volume_exporter = VolumeExporter()
        self.packager = Packager()
        self.storage = self.settings.get_storage_driver()

    async def create_job(self, stack_id: str, triggered_by: str = "manual") -> BackupJob:
        async with AsyncSessionLocal() as db:
            job = BackupJob(
                stack_id=stack_id,
                stack_name="Initializing...",
                status="pending",
                triggered_by=triggered_by
            )
            db.add(job)
            await db.commit()
            await db.refresh(job)
            return job

    async def run_job(self, job_id: str):
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(BackupJob).where(BackupJob.id == job_id))
            job = result.scalar_one_or_none()
            if not job:
                return

            temp_dir = Path(tempfile.mkdtemp())
            try:
                job.status = "running"
                await db.commit()

                # 3. Export Stack
                logger.info(f"Exporting stack {job.stack_id}")
                stack_data = await self.stack_exporter.export(job.stack_id, temp_dir)
                job.stack_name = stack_data.get("Name", f"Stack {job.stack_id}")
                await db.commit()

                # 4. Export Volumes
                volume_names = self._get_stack_volumes(job.stack_name)
                self.volume_exporter.export(volume_names, temp_dir)

                # 5. Package
                logger.info(f"Packaging backup for {job.stack_name}")
                bundle_path = self.packager.package(temp_dir, stack_data, volume_names)
                
                # 6. Upload
                logger.info(f"Uploading backup {bundle_path.name}")
                storage_path = await self.storage.upload(bundle_path, bundle_path.name)

                # 7. Finalize job
                job.status = "success"
                job.storage_path = storage_path
                job.size_bytes = bundle_path.stat().st_size
                job.completed_at = datetime.utcnow()
                await db.commit()
                
                logger.info(f"Backup job {job.id} completed successfully")
                await notifier.on_success(job)

            except Exception as e:
                logger.error(f"Backup job {job.id} failed: {e}", exc_info=True)
                job.status = "failed"
                job.error_message = str(e)
                job.completed_at = datetime.utcnow()
                await db.commit()
                await notifier.on_failure(job)
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

    def _get_stack_volumes(self, stack_name: str) -> list:
        # Simple heuristic: find volumes where name starts with stack name
        # In production, we'd inspect the containers.
        volumes = []
        try:
            client = self.volume_exporter.client
            for vol in client.volumes.list():
                if vol.name.startswith(f"{stack_name}_") or vol.name == stack_name:
                    volumes.append(vol.name)
        except:
            pass
        return volumes
