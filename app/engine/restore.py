import tarfile
import json
import shutil
import tempfile
import logging
import time
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
        Stops the stack's containers, restores data, then restarts them.
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
        stopped_containers = []

        try:
            logger.info(f"=== RESTORE START: {bundle_path} ===")

            if not bundle_path.exists():
                results["error"] = f"Backup file not found: {bundle_path}"
                return results

            # 1. Unpack bundle
            logger.info("  Unpacking tar.gz...")
            with tarfile.open(bundle_path, "r:gz") as tar:
                tar.extractall(path=temp_dir)

            # 2. Find manifest
            manifest_path = temp_dir / "manifest.json"
            if not manifest_path.exists():
                candidates = list(temp_dir.rglob("manifest.json"))
                if candidates:
                    manifest_path = candidates[0]
                else:
                    results["error"] = "Invalid backup: manifest.json missing"
                    return results

            with open(manifest_path, "r") as f:
                manifest = json.load(f)

            stack_data = manifest.get("stack", {})
            stack_name = stack_data.get("name", "unknown")
            results["stack_name"] = stack_name
            volumes_base = manifest_path.parent / "volumes"

            # 2.5 Update stack definition in Portainer (if available)
            logger.info(f"  Updating stack definition in Portainer...")
            update_msg = self._update_portainer_stack(stack_data, temp_dir)
            results["details"].append(update_msg)
            logger.info(f"    {update_msg}")

            volume_list = manifest.get("volumes", [])
            results["volumes_found"] = len(volume_list)

            if not volume_list:
                results["error"] = "No volumes recorded in this backup"
                results["status"] = "empty"
                return results

            # 3. STOP all containers belonging to this stack
            logger.info(f"  Stopping stack '{stack_name}' containers...")
            stopped_containers = self._stop_stack_containers(stack_name)
            if stopped_containers:
                results["details"].append(f"Stopped {len(stopped_containers)} containers")
                # Give containers a moment to fully release file handles
                time.sleep(2)
            else:
                results["details"].append("⚠️ No running containers found for this stack — restoring anyway")

            # 4. Ensure alpine
            self._ensure_alpine()

            # 5. Restore each volume
            for vol_info in volume_list:
                vol_name = vol_info if isinstance(vol_info, str) else vol_info.get("name", str(vol_info))
                tar_file = volumes_base / f"{vol_name}.tar"

                if not tar_file.exists():
                    msg = f"Volume '{vol_name}': tar not found in backup"
                    logger.warning(f"  {msg}")
                    results["details"].append(msg)
                    continue

                tar_size = tar_file.stat().st_size
                if tar_size < 100:
                    msg = f"Volume '{vol_name}': file too small ({tar_size}B), skipped"
                    logger.warning(f"  {msg}")
                    results["details"].append(msg)
                    continue

                try:
                    self._restore_volume(vol_name, tar_file)
                    size_mb = round(tar_size / (1024 * 1024), 1)
                    msg = f"Volume '{vol_name}': restored ({size_mb} MB)"
                    results["details"].append(msg)
                    results["volumes_restored"] += 1
                except Exception as e:
                    msg = f"Volume '{vol_name}': FAILED — {e}"
                    logger.error(f"  {msg}", exc_info=True)
                    results["details"].append(msg)

            # 6. RESTART the stopped containers
            if stopped_containers:
                logger.info(f"  Restarting {len(stopped_containers)} containers...")
                restarted = self._start_containers(stopped_containers)
                results["details"].append(f"Restarted {restarted} containers")

            # Set status
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
            # Try to restart containers even if restore failed
            if stopped_containers:
                logger.info("  Restarting containers after failure...")
                self._start_containers(stopped_containers)
                results["details"].append("Containers restarted after failure")
            return results
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _update_portainer_stack(self, stack_data: dict, temp_dir: Path) -> str:
        """Update the stack's compose file in Portainer using the API."""
        stack_id = stack_data.get("Id")
        endpoint_id = stack_data.get("EndpointId")
        
        if not stack_id or not endpoint_id:
            return "Missing stack ID or endpoint ID in backup manifest. Skipped Portainer update."
            
        stack_dir = temp_dir / "stack"
        compose_file = stack_dir / "docker-compose.yml"
        env_file = stack_dir / "stack.env"
        
        if not compose_file.exists():
            return "No docker-compose.yml found in backup. Skipped Portainer update."
            
        compose_content = compose_file.read_text(encoding="utf-8")
        env_vars = []
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    env_vars.append({"name": k.strip(), "value": v.strip()})
                    
        import requests
        url = f"{self.settings.PORTAINER_URL.rstrip('/')}/api/stacks/{stack_id}?endpointId={endpoint_id}"
        try:
            resp = requests.put(
                url,
                headers={"X-API-Key": self.settings.PORTAINER_API_TOKEN},
                json={
                    "stackFileContent": compose_content,
                    "env": env_vars,
                    "prune": True,
                    "pullImage": False
                },
                verify=(self.settings.PORTAINER_SSL_VERIFY.lower() == "true"),
                timeout=30.0
            )
            if resp.status_code == 404:
                return f"Stack {stack_id} no longer exists in Portainer. Could not update definition."
            resp.raise_for_status()
            return f"Portainer stack definition updated successfully."
        except Exception as e:
            return f"Failed to update Portainer stack definition: {e}"

    def _stop_stack_containers(self, stack_name: str) -> list:
        """Stop all containers belonging to a stack. Returns list of stopped container IDs."""
        stopped = []
        # Try multiple name variants (same as volume detection)
        name_variants = list(dict.fromkeys([
            stack_name.lower(),
            stack_name,
            stack_name.lower().replace(" ", ""),
            stack_name.lower().replace(" ", "-"),
        ]))

        for label_name in name_variants:
            containers = self.docker_client.containers.list(
                filters={"label": f"com.docker.compose.project={label_name}"}
            )
            if containers:
                for c in containers:
                    try:
                        logger.info(f"    Stopping container: {c.name}")
                        c.stop(timeout=30)
                        stopped.append(c.id)
                    except Exception as e:
                        logger.error(f"    Failed to stop {c.name}: {e}")
                break

        logger.info(f"  Stopped {len(stopped)} containers")
        return stopped

    def _start_containers(self, container_ids: list) -> int:
        """Restart previously stopped containers. Returns count of started."""
        started = 0
        for cid in container_ids:
            try:
                c = self.docker_client.containers.get(cid)
                logger.info(f"    Starting container: {c.name}")
                c.start()
                started += 1
            except Exception as e:
                logger.error(f"    Failed to start container {cid}: {e}")
        return started

    def _ensure_alpine(self):
        try:
            self.docker_client.images.get("alpine")
        except docker.errors.ImageNotFound:
            logger.info("  Pulling alpine image...")
            self.docker_client.images.pull("alpine")

    def _restore_volume(self, vol_name: str, tar_file: Path):
        logger.info(f"  Restoring volume: {vol_name}")

        try:
            self.docker_client.volumes.get(vol_name)
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
    """Inspect a backup file without restoring it."""
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
