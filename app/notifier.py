import logging
from app.models import BackupJob
from app.config import get_settings

logger = logging.getLogger(__name__)

class Notifier:
    def __init__(self):
        self.settings = get_settings()

    def _get_notif_config(self) -> dict:
        """Load effective notification config (env + runtime overrides)."""
        return self.settings.get_effective_notification_config()

    async def on_success(self, job: BackupJob):
        msg = f"✅ Backup successful for stack '{job.stack_name}'\nSize: {job.size_bytes / (1024*1024):.2f} MB"
        await self._notify_all(msg, job)

    async def on_failure(self, job: BackupJob):
        msg = f"❌ Backup FAILED for stack '{job.stack_name}'\nError: {job.error_message}"
        await self._notify_all(msg, job)

    async def _notify_all(self, message: str, job: BackupJob):
        config = self._get_notif_config()

        # Apprise
        apprise_cfg = config.get("apprise", {})
        if apprise_cfg.get("enabled") and apprise_cfg.get("urls"):
            await self._send_apprise(message, apprise_cfg["urls"])

    async def _send_apprise(self, message: str, urls: str):
        try:
            import apprise
            apobj = apprise.Apprise()
            for url in urls.split(","):
                url = url.strip()
                if url:
                    apobj.add(url)
            
            subject = message.splitlines()[0] if message.splitlines() else "Backtainer Notification"
            await apobj.async_notify(
                body=message,
                title=f"Backtainer: {subject}",
            )
            logger.info("Apprise notification sent")
        except Exception as e:
            logger.error(f"Failed to send Apprise notification: {e}")

# Singleton
notifier = Notifier()
