import tarfile
import json
import shutil
import tempfile
import logging
import docker
from pathlib import Path
from app.config import get_settings

logger = logging.getLogger(__name__)


class RestoreEngine:
    def __init__(self):
        self.settings = get_settings()
        self.docker_client = docker.from_env()

    def restore(self, bundle_path: Path) -> dict:
        """
        Restore volumes from a backup bundle.
        This is intentionally synchronous because the Docker SDK is synchronous.
        It is called from BackgroundTasks which runs it in a thread.
        """
        temp_dir = Path(tempfile.mkdtemp())
        try:
            logger.info(f"Starting restore from {bundle_path}")

            if not bundle_path.exists():
                raise FileNotFoundError(f"Backup file not found: {bundle_path}")

            # 1. Unpack bundle
            with tarfile.open(bundle_path, "r:gz") as tar:
                tar.extractall(path=temp_dir)

            # 2. Read manifest — it may be at root or inside a '.' directory
            manifest_path = temp_dir / "manifest.json"
            if not manifest_path.exists():
                # The packager uses arcname="." so files may be directly in temp_dir
                # or in a subdirectory. Search for it.
                candidates = list(temp_dir.rglob("manifest.json"))
                if candidates:
                    manifest_path = candidates[0]
                else:
                    raise ValueError("Invalid backup bundle: manifest.json missing")

            with open(manifest_path, "r") as f:
                manifest = json.load(f)

            stack_name = manifest["stack"]["name"]
            # The volumes dir is relative to wherever manifest.json lives
            volumes_base = manifest_path.parent / "volumes"
            logger.info(f"Restoring stack: {stack_name}")
            logger.info(f"Looking for volume tars in: {volumes_base}")

            # 3. Ensure alpine image is available
            self._ensure_alpine()

            # 4. Restore Volumes
            restored_count = 0
            for vol_info in manifest.get("volumes", []):
                vol_name = vol_info if isinstance(vol_info, str) else vol_info.get("name", vol_info)
                tar_file = volumes_base / f"{vol_name}.tar"

                if tar_file.exists():
                    self._restore_volume(vol_name, tar_file)
                    restored_count += 1
                else:
                    logger.warning(f"Volume tar not found: {tar_file}, skipping")

            logger.info(f"Restore complete for {stack_name}: {restored_count} volumes restored")
            return {"stack_name": stack_name, "volumes_restored": restored_count}

        except Exception as e:
            logger.error(f"Restore failed: {e}", exc_info=True)
            raise
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _ensure_alpine(self):
        """Pull alpine image if it doesn't exist locally."""
        try:
            self.docker_client.images.get("alpine")
        except docker.errors.ImageNotFound:
            logger.info("Pulling alpine image for restore...")
            self.docker_client.images.pull("alpine")

    def _restore_volume(self, vol_name: str, tar_file: Path):
        """Restore a single volume from a tar file."""
        logger.info(f"Restoring volume: {vol_name}")

        # Ensure volume exists
        try:
            self.docker_client.volumes.get(vol_name)
            logger.info(f"Volume {vol_name} exists, will overwrite data")
        except docker.errors.NotFound:
            self.docker_client.volumes.create(vol_name)
            logger.info(f"Created new volume: {vol_name}")

        # Restore data using a temp container
        container = self.docker_client.containers.create(
            "alpine",
            command="sleep 3600",
            volumes={vol_name: {"bind": "/data", "mode": "rw"}},
            auto_remove=False
        )

        try:
            container.start()

            # The tar from get_archive("/data") wraps files under a "data/" prefix.
            # put_archive("/") will place "data/..." at "/data/..." which is correct.
            with open(tar_file, "rb") as f:
                container.put_archive("/", f)

            logger.info(f"Successfully restored data to volume {vol_name}")
        finally:
            container.remove(force=True)
