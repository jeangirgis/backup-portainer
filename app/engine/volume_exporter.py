import docker
import os
import logging
from pathlib import Path
from typing import List
from app.exceptions import DockerSocketError

logger = logging.getLogger(__name__)

class VolumeExporter:
    def __init__(self):
        try:
            self.client = docker.from_env()
        except Exception as e:
            raise DockerSocketError(f"Failed to connect to Docker socket: {e}")

    def export(self, volume_names: List[str], output_dir: Path):
        volumes_dir = output_dir / "volumes"
        volumes_dir.mkdir(parents=True, exist_ok=True)

        for vol_name in volume_names:
            try:
                logger.info(f"Exporting volume: {vol_name}")
                
                # Explicitly pull alpine to ensure it exists
                try:
                    self.client.images.get("alpine")
                except docker.errors.ImageNotFound:
                    logger.info("Pulling alpine image for volume export...")
                    self.client.images.pull("alpine")

                # Create a temporary container to access the volume
                container = self.client.containers.create(
                    "alpine",
                    command="sleep 3600",
                    volumes={vol_name: {"bind": "/data", "mode": "ro"}},
                    auto_remove=False
                )
                
                try:
                    container.start()
                    bits, stat = container.get_archive("/data")
                    
                    tar_path = volumes_dir / f"{vol_name}.tar"
                    with open(tar_path, "wb") as f:
                        for chunk in bits:
                            f.write(chunk)
                    
                    logger.info(f"Successfully exported {vol_name} ({stat['size']} bytes)")
                
                finally:
                    container.remove(force=True)

            except docker.errors.NotFound:
                logger.warning(f"Volume {vol_name} not found, skipping...")
            except Exception as e:
                logger.error(f"Failed to export volume {vol_name}: {e}")
                # We don't raise here as per instructions: skip but don't abort
