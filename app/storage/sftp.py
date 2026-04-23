import paramiko
import asyncio
import os
from pathlib import Path
from typing import List, Dict
from datetime import datetime
from app.storage.base import StorageDriver
from app.exceptions import SFTPConnectionError

class SFTPDriver(StorageDriver):
    def __init__(self, host: str, port: int, user: str, password: str = None, 
                 key_path: str = None, remote_dir: str = "/backups"):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.key_path = key_path
        self.remote_dir = remote_dir

    def _get_client(self):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        if self.key_path:
            key = paramiko.RSAKey.from_private_key_file(self.key_path)
            client.connect(self.host, self.port, self.user, pkey=key)
        else:
            client.connect(self.host, self.port, self.user, self.password)
        
        return client

    async def upload(self, local_path: Path, remote_name: str) -> str:
        loop = asyncio.get_event_loop()
        try:
            def _do_upload():
                with self._get_client() as client:
                    sftp = client.open_sftp()
                    try:
                        sftp.chdir(self.remote_dir)
                    except IOError:
                        sftp.mkdir(self.remote_dir)
                        sftp.chdir(self.remote_dir)
                    
                    sftp.put(str(local_path), remote_name)
                    return os.path.join(self.remote_dir, remote_name)
            
            return await loop.run_in_executor(None, _do_upload)
        except Exception as e:
            raise SFTPConnectionError(f"SFTP upload failed: {e}")

    async def download(self, remote_path: str, local_path: Path) -> None:
        loop = asyncio.get_event_loop()
        try:
            def _do_download():
                with self._get_client() as client:
                    sftp = client.open_sftp()
                    sftp.get(remote_path, str(local_path))
            
            await loop.run_in_executor(None, _do_download)
        except Exception as e:
            raise SFTPConnectionError(f"SFTP download failed: {e}")

    async def delete(self, remote_path: str) -> None:
        loop = asyncio.get_event_loop()
        try:
            def _do_delete():
                with self._get_client() as client:
                    sftp = client.open_sftp()
                    sftp.remove(remote_path)
            
            await loop.run_in_executor(None, _do_delete)
        except Exception as e:
            pass

    async def list_backups(self) -> List[Dict]:
        loop = asyncio.get_event_loop()
        try:
            def _do_list():
                with self._get_client() as client:
                    sftp = client.open_sftp()
                    try:
                        sftp.chdir(self.remote_dir)
                    except IOError:
                        return []
                    
                    backups = []
                    for attr in sftp.listdir_attr():
                        if attr.filename.endswith(".tar.gz"):
                            backups.append({
                                "name": attr.filename,
                                "size": attr.st_size,
                                "modified_at": datetime.fromtimestamp(attr.st_mtime)
                            })
                    return backups
            
            return await loop.run_in_executor(None, _do_list)
        except Exception:
            return []
