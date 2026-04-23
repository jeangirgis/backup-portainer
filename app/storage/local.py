import shutil
import os
from pathlib import Path
from typing import List, Dict
from datetime import datetime
from app.storage.base import StorageDriver

class LocalDriver(StorageDriver):
    def __init__(self, backup_dir: Path):
        self.backup_dir = backup_dir
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    async def upload(self, local_path: Path, remote_name: str) -> str:
        dest_path = self.backup_dir / remote_name
        shutil.copy2(local_path, dest_path)
        return str(remote_name)

    async def download(self, remote_path: str, local_path: Path) -> None:
        src_path = self.backup_dir / remote_path
        shutil.copy2(src_path, local_path)

    async def delete(self, remote_path: str) -> None:
        path = self.backup_dir / remote_path
        if path.exists():
            os.remove(path)

    async def list_backups(self) -> List[Dict]:
        backups = []
        for file in self.backup_dir.glob("*.tar.gz"):
            stats = file.stat()
            backups.append({
                "name": file.name,
                "size": stats.st_size,
                "modified_at": datetime.fromtimestamp(stats.st_mtime)
            })
        return backups
