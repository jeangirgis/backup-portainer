import tarfile
import json
import shutil
import tempfile
import logging
import os
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
        Fully synchronous — uses Docker SDK which is synchronous.
        Returns detailed result dict.
        """
        temp_dir = Path(tempfile.mkdtemp())
        results = {
            "status": "failed",
            "stack_name": "unknown",
            "volumes_found": 0,
            "volumes_restored": 0,
            "details": [],
            "error": None,
        }

        try:
            logger.info(f"=== RESTORE START: {bundle_path} ===")
            logger.info(f"  File exists: {bundle_path.exists()}")
            if bundle_path.exists():
                logger.info(f"  File size: {bundle_path.stat().st_size} bytes")

            if not bundle_path.exists():
                results["error"] = f"Backup file not found: {bundle_path}"
                return results

            # 1. Unpack bundle
            logger.info("  Unpacking tar.gz...")
            with tarfile.open(bundle_path, "r:gz") as tar:
                tar.extractall(path=temp_dir)

            # Log extracted contents
            for p in sorted(temp_dir.rglob("*")):
                size = p.stat().st_size if p.is_file() else 0
                logger.info(f"  Extracted: {p.relative_to(temp_dir)} ({size} bytes)")

            # 2. Find manifest
            manifest_path = temp_dir / "manifest.json"
            if not manifest_path.exists():
                candidates = list(temp_dir.rglob("manifest.json"))
                if candidates:
                    manifest_path = candidates[0]
                    logger.info(f"  Found manifest at: {manifest_path.relative_to(temp_dir)}")
                else:
                    results["error"] = "Invalid backup: manifest.json missing"
                    return results

            with open(manifest_path, "r") as f:
                manifest = json.load(f)

            stack_name = manifest.get("stack", {}).get("name", "unknown")
            results["stack_name"] = stack_name
            volumes_base = manifest_path.parent / "volumes"

            volume_list = manifest.get("volumes", [])
            results["volumes_found"] = len(volume_list)
            logger.info(f"  Stack: {stack_name}")
            logger.info(f"  Volumes in manifest: {volume_list}")
            logger.info(f"  Volumes dir exists: {volumes_base.exists()}")

            if volumes_base.exists():
                for f in volumes_base.iterdir():
                    logger.info(f"  Volume tar: {f.name} ({f.stat().st_size} bytes)")

            if not volume_list:
                results["error"] = "No volumes recorded in this backup"
                results["status"] = "partial"
                results["details"].append("Stack compose file was restored but no volume data was included in this backup.")
                return results

            # 3. Ensure alpine
            self._ensure_alpine()

            # 4. Restore each volume
            for vol_info in volume_list:
                vol_name = vol_info if isinstance(vol_info, str) else vol_info.get("name", str(vol_info))
                tar_file = volumes_base / f"{vol_name}.tar"

                if not tar_file.exists():
                    msg = f"Volume '{vol_name}': tar file not found in backup"
                    logger.warning(f"  {msg}")
                    results["details"].append(msg)
                    continue

                tar_size = tar_file.stat().st_size
                if tar_size < 100:
                    msg = f"Volume '{vol_name}': tar file too small ({tar_size} bytes), likely empty"
                    logger.warning(f"  {msg}")
                    results["details"].append(msg)
                    continue

                try:
                    self._restore_volume(vol_name, tar_file)
                    msg = f"Volume '{vol_name}': restored successfully ({tar_size} bytes)"
                    logger.info(f"  {msg}")
                    results["details"].append(msg)
                    results["volumes_restored"] += 1
                except Exception as e:
                    msg = f"Volume '{vol_name}': restore failed — {e}"
                    logger.error(f"  {msg}", exc_info=True)
                    results["details"].append(msg)

            if results["volumes_restored"] > 0:
                results["status"] = "success"
            elif results["volumes_found"] > 0:
                results["status"] = "partial"
            else:
                results["status"] = "empty"

            logger.info(f"=== RESTORE COMPLETE: {results['volumes_restored']}/{results['volumes_found']} volumes ===")
            return results

        except Exception as e:
            logger.error(f"=== RESTORE FAILED: {e} ===", exc_info=True)
            results["error"] = str(e)
            return results
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _ensure_alpine(self):
        try:
            self.docker_client.images.get("alpine")
        except docker.errors.ImageNotFound:
            logger.info("  Pulling alpine image...")
            self.docker_client.images.pull("alpine")

    def _restore_volume(self, vol_name: str, tar_file: Path):
        logger.info(f"  Restoring volume: {vol_name}")

        # Ensure volume exists
        try:
            self.docker_client.volumes.get(vol_name)
            logger.info(f"  Volume {vol_name} exists, overwriting...")
        except docker.errors.NotFound:
            self.docker_client.volumes.create(vol_name)
            logger.info(f"  Created volume: {vol_name}")

        container = self.docker_client.containers.create(
            "alpine",
            command="sleep 3600",
            volumes={vol_name: {"bind": "/data", "mode": "rw"}},
            auto_remove=False,
        )

        try:
            container.start()
            with open(tar_file, "rb") as f:
                container.put_archive("/", f)
            logger.info(f"  Data written to volume {vol_name}")
        finally:
            container.remove(force=True)


def inspect_backup(bundle_path: Path) -> dict:
    """Inspect a backup file without restoring it. Returns manifest + file listing."""
    temp_dir = Path(tempfile.mkdtemp())
    try:
        with tarfile.open(bundle_path, "r:gz") as tar:
            tar.extractall(path=temp_dir)

        manifest_path = temp_dir / "manifest.json"
        if not manifest_path.exists():
            candidates = list(temp_dir.rglob("manifest.json"))
            manifest_path = candidates[0] if candidates else None

        manifest = {}
        if manifest_path:
            with open(manifest_path, "r") as f:
                manifest = json.load(f)

        files = []
        for p in sorted(temp_dir.rglob("*")):
            if p.is_file():
                files.append({
                    "path": str(p.relative_to(temp_dir)),
                    "size": p.stat().st_size,
                })

        return {"manifest": manifest, "files": files}
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
