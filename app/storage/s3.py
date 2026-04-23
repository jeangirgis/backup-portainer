import boto3
import asyncio
from pathlib import Path
from typing import List, Dict
from datetime import datetime
from app.storage.base import StorageDriver
from app.exceptions import S3AuthError, S3BucketError

class S3Driver(StorageDriver):
    def __init__(self, bucket: str, access_key: str, secret_key: str, 
                 endpoint_url: str = None, region: str = "us-east-1", prefix: str = "backups/"):
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        
        self.s3 = boto3.client(
            's3',
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            endpoint_url=endpoint_url,
            region_name=region
        )

    async def upload(self, local_path: Path, remote_name: str) -> str:
        key = f"{self.prefix}/{remote_name}" if self.prefix else remote_name
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                None, 
                lambda: self.s3.upload_file(str(local_path), self.bucket, key)
            )
            return key
        except Exception as e:
            raise S3AuthError(f"S3 upload failed: {e}")

    async def download(self, remote_path: str, local_path: Path) -> None:
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: self.s3.download_file(self.bucket, remote_path, str(local_path))
            )
        except Exception as e:
            raise S3BucketError(f"S3 download failed: {e}")

    async def delete(self, remote_path: str) -> None:
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: self.s3.delete_object(Bucket=self.bucket, Key=remote_path)
            )
        except Exception as e:
            logger.error(f"S3 delete failed: {e}")

    async def list_backups(self) -> List[Dict]:
        loop = asyncio.get_event_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self.s3.list_objects_v2(Bucket=self.bucket, Prefix=self.prefix)
            )
            backups = []
            for obj in resp.get('Contents', []):
                backups.append({
                    "name": Path(obj['Key']).name,
                    "size": obj['Size'],
                    "modified_at": obj['LastModified']
                })
            return backups
        except Exception as e:
            return []
