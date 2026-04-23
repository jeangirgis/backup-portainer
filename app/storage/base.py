from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Dict

class StorageDriver(ABC):
    @abstractmethod
    async def upload(self, local_path: Path, remote_name: str) -> str:
        """Upload file. Returns storage path/key."""
        pass

    @abstractmethod
    async def download(self, remote_path: str, local_path: Path) -> None:
        """Download file to local_path."""
        pass

    @abstractmethod
    async def delete(self, remote_path: str) -> None:
        """Delete a backup file."""
        pass

    @abstractmethod
    async def list_backups(self) -> List[Dict]:
        """Return list of {name, size, modified_at} dicts."""
        pass
