import tarfile
import json
import shutil
import tempfile
import logging
import docker
import httpx
from pathlib import Path
from app.config import get_settings

logger = logging.getLogger(__name__)

class RestoreEngine:
    def __init__(self):
        self.settings = get_settings()
        self.docker_client = docker.from_env()

    async def restore(self, bundle_path: Path):
        temp_dir = Path(tempfile.mkdtemp())
        try:
            # 1. Unpack bundle
            with tarfile.open(bundle_path, "r:gz") as tar:
                tar.extractall(path=temp_dir)
            
            # 2. Read manifest
            manifest_path = temp_dir / "manifest.json"
            if not manifest_path.exists():
                raise ValueError("Invalid backup bundle: manifest.json missing")
            
            with open(manifest_path, "r") as f:
                manifest = json.load(f)
            
            stack_name = manifest["stack"]["name"]
            logger.info(f"Restoring stack: {stack_name}")

            # 3. Restore Volumes
            for vol_info in manifest.get("volumes", []):
                vol_name = vol_info if isinstance(vol_info, str) else vol_info["name"]
                tar_file = temp_dir / "volumes" / f"{vol_name}.tar"
                
                if tar_file.exists():
                    await self._restore_volume(vol_name, tar_file)
            
            # 4. (Optional) Re-deploy stack via Portainer API
            # This would require finding the endpoint ID and using the stack creation API.
            # For v1, we focus on data restoration. User can manually redeploy compose if needed.
            logger.info(f"Successfully restored data for {stack_name}")
            return manifest

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    async def _restore_volume(self, vol_name: str, tar_file: Path):
        logger.info(f"Restoring volume: {vol_name}")
        
        # Ensure volume exists
        try:
            self.docker_client.volumes.get(vol_name)
        except docker.errors.NotFound:
            self.docker_client.volumes.create(vol_name)

        # Restore data using a temp container
        container = self.docker_client.containers.create(
            "alpine",
            command="sleep 3600",
            volumes={vol_name: {"bind": "/data", "mode": "rw"}},
            auto_remove=False
        )
        
        try:
            container.start()
            with open(tar_file, "rb") as f:
                container.put_archive("/data", f)
            logger.info(f"Successfully restored data to volume {vol_name}")
        finally:
            container.remove(force=True)
