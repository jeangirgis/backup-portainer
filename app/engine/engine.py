import tempfile
import shutil
import logging
from datetime import datetime
from pathlib import Path
from sqlalchemy import select
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
            ssl_verify=self.settings.PORTAINER_SSL_VERIFY,
        )
        self.volume_exporter = VolumeExporter()
        self.packager = Packager()

    async def create_job(self, stack_id: str, triggered_by: str = "manual") -> BackupJob:
        async with AsyncSessionLocal() as db:
            job = BackupJob(
                stack_id=stack_id,
                stack_name="Initializing...",
                status="pending",
                triggered_by=triggered_by,
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

                # Export Stack
                logger.info(f"=== BACKUP START for stack_id={job.stack_id} ===")
                stack_data = await self.stack_exporter.export(job.stack_id, temp_dir)
                stack_name = stack_data.get("Name", f"Stack {job.stack_id}")
                job.stack_name = stack_name
                await db.commit()
                logger.info(f"Stack name resolved: '{stack_name}'")

                # Find & Export Volumes
                volume_names = self._get_stack_volumes(stack_name)
                logger.info(f"=== VOLUME EXPORT: {len(volume_names)} volumes to export: {volume_names} ===")
                self.volume_exporter.export(volume_names, temp_dir)

                # Log what's in temp_dir before packaging
                for p in sorted(temp_dir.rglob("*")):
                    size = p.stat().st_size if p.is_file() else 0
                    logger.info(f"  TEMP: {p.relative_to(temp_dir)} ({size} bytes)")

                # Package
                bundle_path = self.packager.package(temp_dir, stack_data, volume_names)
                logger.info(f"Bundle created: {bundle_path.name} ({bundle_path.stat().st_size} bytes)")

                # Upload
                storage = self.settings.get_storage_driver()
                storage_path = await storage.upload(bundle_path, bundle_path.name)

                # Finalize
                job.status = "success"
                job.storage_path = storage_path
                job.size_bytes = bundle_path.stat().st_size
                job.completed_at = datetime.utcnow()
                await db.commit()

                logger.info(f"=== BACKUP COMPLETE: {job.id} — {job.size_bytes} bytes ===")
                await notifier.on_success(job)

            except Exception as e:
                logger.error(f"=== BACKUP FAILED: {job.id} — {e} ===", exc_info=True)
                job.status = "failed"
                job.error_message = str(e)
                job.completed_at = datetime.utcnow()
                await db.commit()
                await notifier.on_failure(job)
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

    def _get_stack_volumes(self, stack_name: str) -> list:
        """Find all Docker volumes belonging to a stack using multiple strategies."""
        volumes = []
        client = self.volume_exporter.client

        # Docker Compose v2 normalizes project names to lowercase but KEEPS hyphens
        # Try multiple possible normalizations
        name_variants = list(dict.fromkeys([
            stack_name.lower(),                          # "my-stack" -> "my-stack"
            stack_name,                                   # original casing
            stack_name.lower().replace(" ", ""),          # strip spaces only
            stack_name.lower().replace(" ", "-"),         # spaces to hyphens
            stack_name.lower().replace("-", ""),          # strip hyphens (old Docker Compose v1)
            stack_name.lower().replace("_", ""),          # strip underscores
        ]))
        logger.info(f"--- Volume detection for stack='{stack_name}' ---")
        logger.info(f"  Name variants to try: {name_variants}")

        # Strategy 1: Inspect running containers by label (most reliable)
        for label_name in name_variants:
            try:
                containers = client.containers.list(
                    all=True,
                    filters={"label": f"com.docker.compose.project={label_name}"},
                )
                if containers:
                    logger.info(f"  FOUND {len(containers)} containers with project={label_name}")
                    for c in containers:
                        for mount in c.attrs.get("Mounts", []):
                            if mount.get("Type") == "volume" and "Name" in mount:
                                vol = mount["Name"]
                                logger.info(f"    Container '{c.name}' -> volume '{vol}'")
                                volumes.append(vol)
                    break
            except Exception as e:
                logger.error(f"  Strategy 1 error with '{label_name}': {e}")

        volumes = list(set(volumes))

        # Strategy 2: Volume labels
        if not volumes:
            for label_name in name_variants:
                try:
                    labeled_vols = client.volumes.list(
                        filters={"label": f"com.docker.compose.project={label_name}"}
                    )
                    if labeled_vols:
                        for v in labeled_vols:
                            logger.info(f"  Label match: volume '{v.name}' (project={label_name})")
                            volumes.append(v.name)
                        break
                except Exception as e:
                    logger.error(f"  Strategy 2 error with '{label_name}': {e}")

        # Strategy 3: Name prefix matching
        if not volumes:
            try:
                all_vols = client.volumes.list()
                logger.info(f"  Strategy 3: scanning {len(all_vols)} volumes by name prefix...")
                for v in all_vols:
                    for variant in name_variants:
                        if (
                            v.name.lower().startswith(f"{variant}_")
                            or v.name.lower().startswith(f"{variant}-")
                        ):
                            logger.info(f"    Name prefix match: '{v.name}' (variant={variant})")
                            volumes.append(v.name)
                            break
            except Exception as e:
                logger.error(f"  Strategy 3 error: {e}")

        # Strategy 4: Dump all volumes for debugging if nothing found
        if not volumes:
            try:
                all_vols = client.volumes.list()
                logger.warning(f"  *** NO VOLUMES FOUND for '{stack_name}' ***")
                logger.warning(f"  All {len(all_vols)} volumes in Docker:")
                for v in all_vols:
                    labels = v.attrs.get("Labels", {}) or {}
                    project = labels.get("com.docker.compose.project", "—")
                    logger.warning(f"    '{v.name}' project_label='{project}'")
            except Exception:
                pass

        result = list(set(volumes))
        logger.info(f"--- Result: {len(result)} volumes: {result} ---")
        return result
