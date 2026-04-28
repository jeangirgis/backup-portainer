from functools import lru_cache
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
import json
import logging

logger = logging.getLogger(__name__)

# Path to runtime config file (saved by UI)
RUNTIME_CONFIG_PATH = None  # Set after settings load

def _get_runtime_config_path():
    global RUNTIME_CONFIG_PATH
    if RUNTIME_CONFIG_PATH is None:
        settings = get_settings()
        RUNTIME_CONFIG_PATH = Path(settings.LOCAL_BACKUP_DIR) / "runtime_config.json"
    return RUNTIME_CONFIG_PATH

def load_runtime_config() -> dict:
    """Load runtime config from JSON file (UI-saved settings)."""
    try:
        path = _get_runtime_config_path()
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load runtime config: {e}")
    return {}

def save_runtime_config(config: dict):
    """Save runtime config to JSON file."""
    try:
        path = _get_runtime_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        logger.info("Runtime config saved successfully")
    except Exception as e:
        logger.error(f"Failed to save runtime config: {e}")
        raise

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

    # Notifications — Email / SMTP
    NOTIFY_EMAIL_TO: Optional[str] = None
    NOTIFY_EMAIL_FROM: Optional[str] = None
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    SMTP_USE_TLS: bool = True

    # Notifications — Slack
    NOTIFY_SLACK_WEBHOOK: Optional[str] = None

    # Notifications — Telegram
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None

    # Notifications — Generic Webhook
    NOTIFY_WEBHOOK_URL: Optional[str] = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    def get_effective_storage_backend(self) -> str:
        """Get the storage backend, considering runtime overrides."""
        rc = load_runtime_config()
        return rc.get("storage", {}).get("backend", self.STORAGE_BACKEND)

    def get_effective_storage_config(self) -> dict:
        """Get the full storage config, merging env vars + runtime overrides."""
        rc = load_runtime_config()
        backend = rc.get("storage", {}).get("backend", self.STORAGE_BACKEND)
        
        # Start with env-var defaults
        configs = {
            "local": {"backup_dir": self.LOCAL_BACKUP_DIR},
            "s3": {
                "bucket": self.S3_BUCKET or "",
                "access_key": self.S3_ACCESS_KEY or "",
                "secret_key": self.S3_SECRET_KEY or "",
                "endpoint_url": self.S3_ENDPOINT_URL or "",
                "region": self.S3_REGION,
                "prefix": self.S3_PREFIX,
            },
            "sftp": {
                "host": self.SFTP_HOST or "",
                "port": self.SFTP_PORT,
                "user": self.SFTP_USER or "",
                "password": self.SFTP_PASSWORD or "",
                "key_path": self.SFTP_KEY_PATH or "",
                "remote_dir": self.SFTP_REMOTE_DIR,
            },
            "gdrive": {
                "folder_id": self.GDRIVE_FOLDER_ID or "",
                "credentials_json": "",
            },
        }

        # Merge runtime overrides on top
        storage_rc = rc.get("storage", {})
        for provider in configs:
            if provider in storage_rc:
                configs[provider].update(storage_rc[provider])

        return {"backend": backend, "configs": configs}

    def get_effective_notification_config(self) -> dict:
        """Get all notification configs, merging env vars + runtime overrides."""
        rc = load_runtime_config()
        
        defaults = {
            "email": {
                "enabled": bool(self.NOTIFY_EMAIL_TO and self.SMTP_HOST),
                "smtp_host": self.SMTP_HOST or "",
                "smtp_port": self.SMTP_PORT,
                "smtp_user": self.SMTP_USER or "",
                "smtp_password": self.SMTP_PASSWORD or "",
                "smtp_use_tls": self.SMTP_USE_TLS,
                "from_address": self.NOTIFY_EMAIL_FROM or "",
                "to_address": self.NOTIFY_EMAIL_TO or "",
            },
            "slack": {
                "enabled": bool(self.NOTIFY_SLACK_WEBHOOK),
                "webhook_url": self.NOTIFY_SLACK_WEBHOOK or "",
            },
            "telegram": {
                "enabled": bool(self.TELEGRAM_BOT_TOKEN and self.TELEGRAM_CHAT_ID),
                "bot_token": self.TELEGRAM_BOT_TOKEN or "",
                "chat_id": self.TELEGRAM_CHAT_ID or "",
            },
            "webhook": {
                "enabled": bool(self.NOTIFY_WEBHOOK_URL),
                "url": self.NOTIFY_WEBHOOK_URL or "",
            },
        }

        # Merge runtime overrides
        notif_rc = rc.get("notifications", {})
        for channel in defaults:
            if channel in notif_rc:
                defaults[channel].update(notif_rc[channel])

        return defaults

    def get_storage_driver(self):
        from app.storage.local import LocalDriver
        from app.storage.s3 import S3Driver
        from app.storage.sftp import SFTPDriver
        from app.storage.gdrive import GoogleDriveDriver

        sc = self.get_effective_storage_config()
        backend = sc["backend"]
        cfg = sc["configs"].get(backend, {})

        if backend == "local":
            return LocalDriver(Path(cfg.get("backup_dir", self.LOCAL_BACKUP_DIR)))
        elif backend == "s3":
            return S3Driver(
                bucket=cfg.get("bucket", self.S3_BUCKET),
                access_key=cfg.get("access_key", self.S3_ACCESS_KEY),
                secret_key=cfg.get("secret_key", self.S3_SECRET_KEY),
                endpoint_url=cfg.get("endpoint_url", self.S3_ENDPOINT_URL) or None,
                region=cfg.get("region", self.S3_REGION),
                prefix=cfg.get("prefix", self.S3_PREFIX),
            )
        elif backend == "sftp":
            return SFTPDriver(
                host=cfg.get("host", self.SFTP_HOST),
                port=int(cfg.get("port", self.SFTP_PORT)),
                user=cfg.get("user", self.SFTP_USER),
                password=cfg.get("password", self.SFTP_PASSWORD),
                key_path=cfg.get("key_path", self.SFTP_KEY_PATH) or None,
                remote_dir=cfg.get("remote_dir", self.SFTP_REMOTE_DIR),
            )
        elif backend == "gdrive":
            gdrive_folder_id = cfg.get("folder_id", self.GDRIVE_FOLDER_ID)
            gdrive_credentials_file = Path(self.GDRIVE_CREDENTIALS_FILE)

            # Check for runtime-saved credentials
            override_file = Path(self.LOCAL_BACKUP_DIR) / "gdrive_config.json"
            if override_file.exists():
                try:
                    with open(override_file, "r") as f:
                        conf = json.load(f)
                        if "folder_id" in conf:
                            gdrive_folder_id = conf["folder_id"]
                        if "credentials_path" in conf:
                            gdrive_credentials_file = Path(conf["credentials_path"])
                except Exception:
                    pass

            if not gdrive_folder_id:
                raise ValueError("GDRIVE_FOLDER_ID must be set when using gdrive storage backend")
            return GoogleDriveDriver(
                credentials_file=gdrive_credentials_file,
                folder_id=gdrive_folder_id
            )
        else:
            raise ValueError(f"Unknown storage backend: {backend}")

@lru_cache()
def get_settings():
    return Settings()
