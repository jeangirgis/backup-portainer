from app.storage.base import StorageDriver
from pathlib import Path
from typing import List, Dict

class S3Driver(StorageDriver):
    def __init__(self, **kwargs):
        pass
    async def upload(self, local_path: Path, remote_name: str) -> str:
        return f"s3://placeholder/{remote_name}"
    async def download(self, remote_path: str, local_path: Path) -> None:
        pass
    async def delete(self, remote_path: str) -> None:
        pass
    async def list_backups(self) -> List[Dict]:
        return []

class SFTPDriver(StorageDriver):
    def __init__(self, **kwargs):
        pass
    async def upload(self, local_path: Path, remote_name: str) -> str:
        return f"sftp://placeholder/{remote_name}"
    async def download(self, remote_path: str, local_path: Path) -> None:
        pass
    async def delete(self, remote_path: str) -> None:
        pass
    async def list_backups(self) -> List[Dict]:
        return []
