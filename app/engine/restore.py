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

    def restore(self, bundle_path: Path, progress_callback=None) -> dict:
        """
        Restore volumes from a backup bundle.
        Stops the stack's containers, restores data, then restarts them.
        
        progress_callback: optional callable(step: str, status: str, detail: str)
            step: step identifier
            status: 'running', 'done', 'error'
            detail: human-readable detail text
        """
        def _progress(step, status, detail=""):
            if progress_callback:
                try:
                    progress_callback(step, status, detail)
                except Exception:
                    pass

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
                _progress("unpack", "error", "Backup file not found")
                return results

            # 1. Unpack bundle
            _progress("unpack", "running", "Extracting backup archive...")
            logger.info("  Unpacking tar.gz...")
            with tarfile.open(bundle_path, "r:gz") as tar:
                tar.extractall(path=temp_dir)
            _progress("unpack", "done", "Backup extracted")

            # 2. Find manifest
            manifest_path = temp_dir / "manifest.json"
            if not manifest_path.exists():
                candidates = list(temp_dir.rglob("manifest.json"))
                if candidates:
                    manifest_path = candidates[0]
                else:
                    results["error"] = "Invalid backup: manifest.json missing"
                    _progress("unpack", "error", "Invalid backup: manifest.json missing")
                    return results

            with open(manifest_path, "r") as f:
                manifest = json.load(f)

            stack_data = manifest.get("stack", {})
            stack_name = stack_data.get("name") or stack_data.get("Name") or "unknown"
            results["stack_name"] = stack_name
            volumes_base = manifest_path.parent / "volumes"

            volume_list = manifest.get("volumes", [])
            results["volumes_found"] = len(volume_list)

            if not volume_list:
                results["error"] = "No volumes recorded in this backup"
                results["status"] = "empty"
                _progress("unpack", "error", "No volumes in backup")
                return results

            # Read stack ID and endpoint ID (handle both old and new manifest formats)
            stack_id = stack_data.get("Id") or stack_data.get("id")
            endpoint_id = stack_data.get("EndpointId")

            # For old backups that don't have EndpointId, look it up from Portainer
            if stack_id and not endpoint_id:
                endpoint_id = self._lookup_endpoint_id(stack_id)
            # If still no endpoint ID, try to get the default endpoint
            if not endpoint_id:
                endpoint_id = self._get_default_endpoint_id()

            # Write resolved values back into stack_data so all helper methods can use them
            stack_data["Id"] = stack_id
            stack_data["EndpointId"] = endpoint_id
            stack_data["name"] = stack_name

            logger.info(f"  Resolved: stack_id={stack_id}, endpoint_id={endpoint_id}, stack_name={stack_name}")

            # 3. STOP the stack
            _progress("stop", "running", f"Stopping stack '{stack_name}'...")
            logger.info(f"  Stopping stack '{stack_name}'...")
            portainer_stopped = False
            if stack_id and endpoint_id:
                stop_msg = self._stop_portainer_stack(stack_id, endpoint_id)
                results["details"].append(stop_msg)
                logger.info(f"    {stop_msg}")
                portainer_stopped = "stopped" in stop_msg.lower() or "already" in stop_msg.lower()

            if not portainer_stopped:
                # Fallback: stop containers directly via Docker
                stopped_containers = self._stop_stack_containers(stack_name)
                if stopped_containers:
                    results["details"].append(f"Stopped {len(stopped_containers)} containers via Docker")
                else:
                    results["details"].append("⚠️ No running containers found for this stack — restoring anyway")

            _progress("stop", "done", "Stack stopped")

            # Give containers a moment to fully release file handles
            time.sleep(3)

            # 4. Ensure alpine
            self._ensure_alpine()

            # 5. Restore each volume
            total_vols = len(volume_list)
            for idx, vol_info in enumerate(volume_list, 1):
                vol_name = vol_info if isinstance(vol_info, str) else vol_info.get("name", str(vol_info))
                tar_file = volumes_base / f"{vol_name}.tar"

                _progress("volumes", "running", f"Restoring volume {idx}/{total_vols}: {vol_name}")

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

            _progress("volumes", "done", f"{results['volumes_restored']}/{total_vols} volumes restored")

            # 6. RESTART the stack via Portainer
            _progress("start", "running", "Starting stack...")
            logger.info(f"  Starting stack '{stack_name}'...")
            portainer_started = False
            if stack_id and endpoint_id:
                # First try to update the definition (if stack still exists)
                update_msg = self._update_portainer_stack_definition(stack_data, temp_dir)
                results["details"].append(update_msg)
                logger.info(f"    {update_msg}")

                # Then try to start
                start_msg = self._start_portainer_stack(stack_id, endpoint_id)
                results["details"].append(start_msg)
                logger.info(f"    {start_msg}")
                portainer_started = "started" in start_msg.lower()

                # If the stack was deleted (404), recreate it from backup
                if not portainer_started and "not found" in start_msg.lower():
                    _progress("start", "running", "Recreating deleted stack...")
                    logger.info(f"  Stack was deleted — recreating from backup...")
                    # Ensure Docker networks from compose file exist before creating the stack
                    net_msg = self._ensure_compose_networks(stack_name, temp_dir)
                    if net_msg:
                        results["details"].append(net_msg)
                        logger.info(f"    {net_msg}")
                    create_msg = self._create_portainer_stack(stack_data, temp_dir)
                    results["details"].append(create_msg)
                    logger.info(f"    {create_msg}")
                    portainer_started = "created" in create_msg.lower() or "deployed" in create_msg.lower()

            if not portainer_started and stopped_containers:
                logger.info(f"  Falling back to direct container restart...")
                restarted = self._start_containers(stopped_containers)
                results["details"].append(f"Restarted {restarted} containers via Docker")

            _progress("start", "done", "Stack started")

            # Set status
            if results["volumes_restored"] > 0:
                results["status"] = "success"
            elif results["volumes_found"] > 0:
                results["status"] = "partial"
            else:
                results["status"] = "empty"

            _progress("complete", "done", f"Restore complete: {results['volumes_restored']}/{results['volumes_found']} volumes")
            logger.info(f"=== RESTORE COMPLETE: {results['volumes_restored']}/{results['volumes_found']} volumes ===")
            return results

        except Exception as e:
            logger.error(f"=== RESTORE FAILED: {e} ===", exc_info=True)
            results["error"] = str(e)
            _progress("complete", "error", str(e))
            # Try to restart the stack even if restore failed
            if stack_id and endpoint_id:
                self._start_portainer_stack(stack_id, endpoint_id)
                results["details"].append("Attempted stack restart after failure")
            elif stopped_containers:
                self._start_containers(stopped_containers)
                results["details"].append("Containers restarted after failure")
            return results
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _lookup_endpoint_id(self, stack_id) -> int:
        """Look up EndpointId for a stack from Portainer API."""
        import requests
        base_url = self.settings.PORTAINER_URL.rstrip('/')
        verify_ssl = self.settings.PORTAINER_SSL_VERIFY.lower() == "true"
        headers = {"X-API-Key": self.settings.PORTAINER_API_TOKEN}
        try:
            resp = requests.get(
                f"{base_url}/api/stacks/{stack_id}",
                headers=headers,
                verify=verify_ssl,
                timeout=10.0
            )
            if resp.status_code == 200:
                data = resp.json()
                eid = data.get("EndpointId")
                logger.info(f"  Looked up EndpointId={eid} for stack {stack_id}")
                return eid
        except Exception as e:
            logger.warning(f"  Failed to look up EndpointId: {e}")
        return None

    def _get_default_endpoint_id(self) -> int:
        """Get the first available endpoint ID from Portainer."""
        import requests
        base_url = self.settings.PORTAINER_URL.rstrip('/')
        verify_ssl = self.settings.PORTAINER_SSL_VERIFY.lower() == "true"
        headers = {"X-API-Key": self.settings.PORTAINER_API_TOKEN}
        try:
            resp = requests.get(
                f"{base_url}/api/endpoints",
                headers=headers,
                verify=verify_ssl,
                timeout=10.0
            )
            if resp.status_code == 200:
                endpoints = resp.json()
                if endpoints:
                    eid = endpoints[0].get("Id")
                    logger.info(f"  Using default EndpointId={eid}")
                    return eid
        except Exception as e:
            logger.warning(f"  Failed to get default endpoint: {e}")
        return None

    def _stop_portainer_stack(self, stack_id, endpoint_id) -> str:
        """Stop a stack via the Portainer API."""
        import requests
        base_url = self.settings.PORTAINER_URL.rstrip('/')
        verify_ssl = self.settings.PORTAINER_SSL_VERIFY.lower() == "true"
        headers = {"X-API-Key": self.settings.PORTAINER_API_TOKEN}

        stop_url = f"{base_url}/api/stacks/{stack_id}/stop?endpointId={endpoint_id}"
        try:
            resp = requests.post(
                stop_url,
                headers=headers,
                verify=verify_ssl,
                timeout=60.0
            )
            if resp.status_code == 400:
                # Stack might already be stopped
                body = resp.json() if resp.headers.get('content-type', '').startswith('application/json') else {}
                detail = body.get("message", body.get("details", ""))
                if "already" in detail.lower() or "inactive" in detail.lower() or "stopped" in detail.lower():
                    return "Stack already stopped."
                return f"Portainer stop returned 400: {detail or resp.text[:200]}"
            if resp.status_code == 404:
                return f"Stack {stack_id} not found in Portainer."
            resp.raise_for_status()
            return "Stack stopped via Portainer."
        except Exception as e:
            return f"Failed to stop stack via Portainer: {e}"

    def _update_portainer_stack_definition(self, stack_data: dict, temp_dir: Path) -> str:
        """Update the stack's compose file in Portainer (without starting it)."""
        stack_id = stack_data.get("Id")
        endpoint_id = stack_data.get("EndpointId")

        stack_dir = temp_dir / "stack"
        compose_file = stack_dir / "docker-compose.yml"
        env_file = stack_dir / "stack.env"

        if not compose_file.exists():
            return "No docker-compose.yml found in backup. Skipped definition update."

        compose_content = compose_file.read_text(encoding="utf-8")
        env_vars = []
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    env_vars.append({"name": k.strip(), "value": v.strip()})

        import requests
        base_url = self.settings.PORTAINER_URL.rstrip('/')
        verify_ssl = self.settings.PORTAINER_SSL_VERIFY.lower() == "true"
        headers = {"X-API-Key": self.settings.PORTAINER_API_TOKEN}

        update_url = f"{base_url}/api/stacks/{stack_id}?endpointId={endpoint_id}"
        try:
            resp = requests.put(
                update_url,
                headers=headers,
                json={
                    "stackFileContent": compose_content,
                    "env": env_vars,
                    "prune": True,
                    "pullImage": False
                },
                verify=verify_ssl,
                timeout=30.0
            )
            if resp.status_code == 404:
                return f"Stack {stack_id} not found. Skipped definition update."
            resp.raise_for_status()
            return "Stack definition updated in Portainer."
        except Exception as e:
            return f"Failed to update stack definition: {e}"

    def _start_portainer_stack(self, stack_id, endpoint_id) -> str:
        """Start a stack via the Portainer API."""
        import requests
        base_url = self.settings.PORTAINER_URL.rstrip('/')
        verify_ssl = self.settings.PORTAINER_SSL_VERIFY.lower() == "true"
        headers = {"X-API-Key": self.settings.PORTAINER_API_TOKEN}

        start_url = f"{base_url}/api/stacks/{stack_id}/start?endpointId={endpoint_id}"
        try:
            resp = requests.post(
                start_url,
                headers=headers,
                verify=verify_ssl,
                timeout=60.0
            )
            if resp.status_code == 400:
                body = resp.json() if resp.headers.get('content-type', '').startswith('application/json') else {}
                detail = body.get("message", body.get("details", ""))
                logger.warning(f"  Portainer start returned 400: {detail}")
                return f"Portainer start returned 400: {detail or resp.text[:200]}"
            if resp.status_code == 404:
                return f"Stack {stack_id} not found in Portainer. Could not start."
            resp.raise_for_status()
            return "Stack started via Portainer."
        except Exception as e:
            return f"Failed to start stack via Portainer: {e}"

    def _create_portainer_stack(self, stack_data: dict, temp_dir: Path) -> str:
        """Create a new stack in Portainer from the backup's compose file.
        Used when the original stack was deleted.
        Falls back to docker compose CLI if the Portainer API fails."""
        endpoint_id = stack_data.get("EndpointId")
        stack_name = stack_data.get("name", "restored-stack")

        if not endpoint_id:
            return "Missing endpoint ID in backup manifest. Cannot create stack."

        stack_dir = temp_dir / "stack"
        compose_file = stack_dir / "docker-compose.yml"

        if not compose_file.exists():
            return "No docker-compose.yml found in backup. Cannot create stack."

        compose_content = compose_file.read_text(encoding="utf-8")
        env_vars = []
        env_file = stack_dir / "stack.env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    env_vars.append({"name": k.strip(), "value": v.strip()})

        import requests
        base_url = self.settings.PORTAINER_URL.rstrip('/')
        verify_ssl = self.settings.PORTAINER_SSL_VERIFY.lower() == "true"
        headers = {"X-API-Key": self.settings.PORTAINER_API_TOKEN}

        create_url = f"{base_url}/api/stacks/create/standalone/string?endpointId={endpoint_id}"
        portainer_error = None
        try:
            resp = requests.post(
                create_url,
                headers=headers,
                json={
                    "Name": stack_name,
                    "StackFileContent": compose_content,
                    "Env": env_vars,
                },
                verify=verify_ssl,
                timeout=120.0
            )
            if resp.status_code == 409:
                return f"A stack named '{stack_name}' already exists. Please remove it first or rename."
            if resp.status_code >= 400:
                # Capture full error body for debugging
                try:
                    err_body = resp.json()
                    err_detail = err_body.get("message") or err_body.get("details") or str(err_body)
                except Exception:
                    err_detail = resp.text[:500]
                portainer_error = f"Portainer API {resp.status_code}: {err_detail}"
                logger.error(f"  Portainer stack create failed: {portainer_error}")
            else:
                return f"Stack '{stack_name}' created and started in Portainer."
        except Exception as e:
            portainer_error = str(e)
            logger.error(f"  Portainer stack create request failed: {e}")

        # --- Fallback: deploy using docker compose CLI ---
        logger.info(f"  Falling back to docker compose CLI for stack '{stack_name}'...")
        cli_result = self._deploy_via_docker_compose(stack_name, stack_dir)
        if cli_result.startswith("Stack '"):
            return cli_result
        # Both methods failed — return combined info
        return f"Failed to create stack via Portainer ({portainer_error}). Docker Compose fallback: {cli_result}"

    def _deploy_via_docker_compose(self, stack_name: str, stack_dir: Path) -> str:
        """Deploy a stack using the docker compose CLI as a fallback."""
        import subprocess
        import os

        compose_file = stack_dir / "docker-compose.yml"
        if not compose_file.exists():
            return "No docker-compose.yml found."

        env_file = stack_dir / "stack.env"
        env = dict(os.environ)
        # Load env vars from stack.env into the subprocess environment
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()

        # Try 'docker compose' (v2 plugin) first, then 'docker-compose' (legacy)
        for cmd_base in [["docker", "compose"], ["docker-compose"]]:
            cmd = cmd_base + [
                "-f", str(compose_file),
                "-p", stack_name,
                "up", "-d", "--remove-orphans"
            ]
            try:
                logger.info(f"    Running: {' '.join(cmd)}")
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    env=env,
                    cwd=str(stack_dir),
                )
                if result.returncode == 0:
                    logger.info(f"    docker compose succeeded: {result.stdout[-300:]}")
                    return f"Stack '{stack_name}' deployed via docker compose CLI."
                else:
                    err = result.stderr[-500:] if result.stderr else result.stdout[-500:]
                    logger.warning(f"    docker compose failed (rc={result.returncode}): {err}")
                    # Continue to try the next command variant
            except FileNotFoundError:
                logger.info(f"    {cmd_base[0]} {'compose' if len(cmd_base) > 1 else ''} not found, trying next...")
                continue
            except subprocess.TimeoutExpired:
                return "docker compose timed out after 120s."
            except Exception as e:
                logger.error(f"    docker compose error: {e}")
                return f"docker compose error: {e}"

        return "docker compose CLI not available on this system."

    def _ensure_compose_networks(self, stack_name: str, temp_dir: Path) -> str:
        """Parse compose file and ensure Docker networks are ready for stack deployment.
        
        If a network exists with incorrect compose labels, it is removed so that
        docker-compose / Portainer can recreate it with the correct metadata.
        If a network doesn't exist, it is created with proper compose labels.
        """
        compose_file = temp_dir / "stack" / "docker-compose.yml"
        if not compose_file.exists():
            return None

        try:
            import yaml
            with open(compose_file, "r", encoding="utf-8") as f:
                compose = yaml.safe_load(f)

            if not compose or not isinstance(compose, dict):
                return None

            project_name = stack_name.lower()
            networks_section = compose.get("networks", {})

            if not networks_section:
                # No explicit networks — handle the default network
                default_net = f"{project_name}_default"
                self._ensure_single_network(default_net, "default", project_name, "bridge")
                return f"Ensured default network: {default_net}"

            created = []
            cleaned = []
            for net_name, net_config in networks_section.items():
                net_config = net_config or {}

                # Determine the actual Docker network name
                if isinstance(net_config, dict) and net_config.get("name"):
                    docker_net_name = net_config["name"]
                else:
                    docker_net_name = f"{project_name}_{net_name}"

                # External networks use the network name directly
                is_external = isinstance(net_config, dict) and net_config.get("external")
                if is_external:
                    if isinstance(net_config.get("external"), dict) and net_config["external"].get("name"):
                        docker_net_name = net_config["external"]["name"]
                    elif isinstance(net_config, dict) and net_config.get("name"):
                        docker_net_name = net_config["name"]
                    else:
                        docker_net_name = net_name
                    # Don't touch external networks — they are managed externally
                    continue

                driver = "bridge"
                if isinstance(net_config, dict) and net_config.get("driver"):
                    driver = net_config["driver"]

                action = self._ensure_single_network(docker_net_name, net_name, project_name, driver)
                if action == "created":
                    created.append(docker_net_name)
                elif action == "cleaned":
                    cleaned.append(docker_net_name)

            parts = []
            if cleaned:
                parts.append(f"Cleaned {len(cleaned)} network(s) with wrong labels: {', '.join(cleaned)}")
            if created:
                parts.append(f"Created {len(created)} network(s): {', '.join(created)}")
            if not parts:
                return "All networks ready."
            return ". ".join(parts)

        except Exception as e:
            logger.error(f"  Failed to ensure networks: {e}", exc_info=True)
            return f"Failed to ensure networks: {e}"

    def _ensure_single_network(self, docker_net_name: str, compose_net_name: str,
                                project_name: str, driver: str) -> str:
        """Ensure a single Docker network is ready for compose deployment.
        Returns: 'exists', 'created', or 'cleaned'."""
        expected_labels = {
            "com.docker.compose.network": compose_net_name,
            "com.docker.compose.project": project_name,
        }

        try:
            net = self.docker_client.networks.get(docker_net_name)
            labels = net.attrs.get("Labels") or {}
            actual_compose_label = labels.get("com.docker.compose.network", "")

            if actual_compose_label == compose_net_name:
                logger.info(f"  Network '{docker_net_name}' exists with correct labels")
                return "exists"

            # Labels are wrong — remove so compose can recreate it correctly
            logger.info(f"  Network '{docker_net_name}' has wrong compose label "
                        f"('{actual_compose_label}' != '{compose_net_name}'), removing...")
            try:
                net.remove()
                logger.info(f"  Removed network '{docker_net_name}'")
                return "cleaned"
            except Exception as e:
                # Network might have connected containers — disconnect them first
                logger.warning(f"  Could not remove network '{docker_net_name}': {e}")
                try:
                    # Force disconnect any remaining containers
                    net.reload()
                    for container_info in (net.attrs.get("Containers") or {}).values():
                        cname = container_info.get("Name", "unknown")
                        try:
                            net.disconnect(cname, force=True)
                            logger.info(f"    Disconnected '{cname}' from '{docker_net_name}'")
                        except Exception:
                            pass
                    net.remove()
                    logger.info(f"  Removed network '{docker_net_name}' after disconnecting containers")
                    return "cleaned"
                except Exception as e2:
                    logger.error(f"  Failed to remove network '{docker_net_name}': {e2}")
                    return "exists"

        except docker.errors.NotFound:
            # Network doesn't exist — create it with proper compose labels
            try:
                self.docker_client.networks.create(
                    docker_net_name,
                    driver=driver,
                    labels=expected_labels
                )
                logger.info(f"  Created network: {docker_net_name} (driver={driver}, labels={expected_labels})")
                return "created"
            except Exception as e:
                logger.error(f"  Failed to create network '{docker_net_name}': {e}")
                return "exists"

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
