import tarfile
import json
import hashlib
import os
from datetime import datetime
from pathlib import Path

class Packager:
    def __init__(self, app_version: str = "1.0.0"):
        self.app_version = app_version

    def _calculate_sha256(self, file_path: Path) -> str:
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return f"sha256:{sha256_hash.hexdigest()}"

    def package(self, temp_dir: Path, stack_data: dict, volumes: list) -> Path:
        timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        stack_name = stack_data.get("Name", "unknown")
        bundle_name = f"backup-{stack_name}-{timestamp}.tar.gz"
        bundle_path = temp_dir.parent / bundle_name

        # 1. Generate manifest.json
        manifest = {
            "version": "1.0",
            "app_version": self.app_version,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "stack": {
                "id": str(stack_data.get("Id")),
                "name": stack_name,
            },
            "volumes": volumes,
            "checksums": {}
        }

        # Calculate checksums for everything in stack/ and volumes/
        for root, dirs, files in os.walk(temp_dir):
            for file in files:
                abs_path = Path(root) / file
                rel_path = abs_path.relative_to(temp_dir)
                manifest["checksums"][str(rel_path)] = self._calculate_sha256(abs_path)

        with open(temp_dir / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        # 2. Create .tar.gz
        with tarfile.open(bundle_path, "w:gz") as tar:
            tar.add(temp_dir, arcname=".")

        return bundle_path
