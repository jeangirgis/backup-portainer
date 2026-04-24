from functools import lru_cache
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path

class Settings(BaseSettings):
    # Required
    PORTAINER_URL: str
    PORTAINER_API_TOKEN: str
    PORTAINER_SSL_VERIFY: str = "true"  # Use string to be safe with Docker env vars
    SECRET_KEY: str

    # Storage
    STORAGE_BACKEND: str = "local"
    LOCAL_BACKUP_DIR: str = "/backups"

    # S3 Storage
    S3_BUCKET: Optional[str] = None
    S3_ACCESS_KEY: Optional[str] = None
    S3_SECRET_KEY: Optional[str] = None
    S3_ENDPOINT_URL: Optional[str] = None
    S3_REGION: str = "us-east-1"
    S3_PREFIX: str = "backups/"

    # SFTP Storage
    SFTP_HOST: Optional[str] = None
    SFTP_PORT: int = 22
    SFTP_USER: Optional[str] = None
    SFTP_PASSWORD: Optional[str] = None
    SFTP_KEY_PATH: Optional[str] = None
    SFTP_REMOTE_DIR: str = "/backups"

    # Google Drive Storage
    GDRIVE_CREDENTIALS_FILE: str = "/app/credentials.json"
    GDRIVE_FOLDER_ID: Optional[str] = None

    # Notifications
    NOTIFY_SLACK_WEBHOOK: Optional[str] = None
    NOTIFY_EMAIL_TO: Optional[str] = None
    NOTIFY_EMAIL_FROM: Optional[str] = None
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    NOTIFY_WEBHOOK_URL: Optional[str] = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    def get_storage_driver(self):
        from app.storage.local import LocalDriver
        from app.storage.s3 import S3Driver
        from app.storage.sftp import SFTPDriver
        from app.storage.gdrive import GoogleDriveDriver

        if self.STORAGE_BACKEND == "local":
            return LocalDriver(Path(self.LOCAL_BACKUP_DIR))
        elif self.STORAGE_BACKEND == "s3":
            return S3Driver(
                bucket=self.S3_BUCKET,
                access_key=self.S3_ACCESS_KEY,
                secret_key=self.S3_SECRET_KEY,
                endpoint_url=self.S3_ENDPOINT_URL,
                region=self.S3_REGION,
                prefix=self.S3_PREFIX
            )
        elif self.STORAGE_BACKEND == "sftp":
            return SFTPDriver(
                host=self.SFTP_HOST,
                port=self.SFTP_PORT,
                user=self.SFTP_USER,
                password=self.SFTP_PASSWORD,
                key_path=self.SFTP_KEY_PATH,
                remote_dir=self.SFTP_REMOTE_DIR
            )
        elif self.STORAGE_BACKEND == "gdrive":
            if not self.GDRIVE_FOLDER_ID:
                raise ValueError("GDRIVE_FOLDER_ID must be set when using gdrive storage backend")
            return GoogleDriveDriver(
                credentials_file=Path(self.GDRIVE_CREDENTIALS_FILE),
                folder_id=self.GDRIVE_FOLDER_ID
            )
        else:
            raise ValueError(f"Unknown storage backend: {self.STORAGE_BACKEND}")

@lru_cache()
def get_settings():
    return Settings()
