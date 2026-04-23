import httpx
import logging
import smtplib
from email.mime.text import MIMEText
from app.models import BackupJob
from app.config import get_settings

logger = logging.getLogger(__name__)

class Notifier:
    def __init__(self):
        self.settings = get_settings()

    async def on_success(self, job: BackupJob):
        msg = f"✅ Backup successful for stack '{job.stack_name}'\nSize: {job.size_bytes / (1024*1024):.2f} MB"
        await self._notify_all(msg, job)

    async def on_failure(self, job: BackupJob):
        msg = f"❌ Backup FAILED for stack '{job.stack_name}'\nError: {job.error_message}"
        await self._notify_all(msg, job)

    async def _notify_all(self, message: str, job: BackupJob):
        # Slack
        if self.settings.NOTIFY_SLACK_WEBHOOK:
            await self._send_slack(message)
        
        # Email
        if self.settings.NOTIFY_EMAIL_TO and self.settings.SMTP_HOST:
            self._send_email(message)
        
        # Generic Webhook
        if self.settings.NOTIFY_WEBHOOK_URL:
            await self._send_webhook(job)

    async def _send_slack(self, message: str):
        try:
            async with httpx.AsyncClient() as client:
                await client.post(self.settings.NOTIFY_SLACK_WEBHOOK, json={"text": message})
        except Exception as e:
            logger.error(f"Failed to send Slack notification: {e}")

    def _send_email(self, message: str):
        try:
            msg = MIMEText(message)
            msg['Subject'] = f"Portainer Backup: {message.splitlines()[0]}"
            msg['From'] = self.settings.NOTIFY_EMAIL_FROM
            msg['To'] = self.settings.NOTIFY_EMAIL_TO

            with smtplib.SMTP(self.settings.SMTP_HOST, self.settings.SMTP_PORT) as server:
                if self.settings.SMTP_USER:
                    server.starttls()
                    server.login(self.settings.SMTP_USER, self.settings.SMTP_PASSWORD)
                server.send_message(msg)
        except Exception as e:
            logger.error(f"Failed to send Email notification: {e}")

    async def _send_webhook(self, job: BackupJob):
        try:
            async with httpx.AsyncClient() as client:
                # Send the whole job object as JSON
                data = {
                    "id": job.id,
                    "stack_name": job.stack_name,
                    "status": job.status,
                    "error_message": job.error_message,
                    "completed_at": job.completed_at.isoformat() if job.completed_at else None
                }
                await client.post(self.settings.NOTIFY_WEBHOOK_URL, json=data)
        except Exception as e:
            logger.error(f"Failed to send generic webhook: {e}")

# Singleton
notifier = Notifier()
