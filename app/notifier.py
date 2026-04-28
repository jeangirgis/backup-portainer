import httpx
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
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

        # Slack
        slack_cfg = config.get("slack", {})
        if slack_cfg.get("enabled") and slack_cfg.get("webhook_url"):
            await self._send_slack(message, slack_cfg["webhook_url"])

        # Email
        email_cfg = config.get("email", {})
        if email_cfg.get("enabled") and email_cfg.get("smtp_host") and email_cfg.get("to_address"):
            self._send_email(message, email_cfg)

        # Telegram
        telegram_cfg = config.get("telegram", {})
        if telegram_cfg.get("enabled") and telegram_cfg.get("bot_token") and telegram_cfg.get("chat_id"):
            await self._send_telegram(message, telegram_cfg)

        # Generic Webhook
        webhook_cfg = config.get("webhook", {})
        if webhook_cfg.get("enabled") and webhook_cfg.get("url"):
            await self._send_webhook(job, webhook_cfg["url"])

    async def _send_slack(self, message: str, webhook_url: str):
        try:
            async with httpx.AsyncClient() as client:
                await client.post(webhook_url, json={"text": message})
            logger.info("Slack notification sent")
        except Exception as e:
            logger.error(f"Failed to send Slack notification: {e}")

    def _send_email(self, message: str, email_cfg: dict):
        try:
            msg = MIMEMultipart("alternative")
            subject_line = message.splitlines()[0] if message.splitlines() else "Portainer Backup Notification"
            msg['Subject'] = f"Portainer Backup: {subject_line}"
            msg['From'] = email_cfg.get("from_address", "")
            msg['To'] = email_cfg.get("to_address", "")

            # Create HTML version for better rendering
            html_body = f"""
            <div style="font-family: Arial, sans-serif; padding: 20px; background: #1a1a2e; color: #e0e0e0; border-radius: 12px;">
                <h2 style="color: #8b5cf6; margin-bottom: 16px;">Portainer Backup Companion</h2>
                <div style="background: #16213e; padding: 16px; border-radius: 8px; border-left: 4px solid #8b5cf6;">
                    <pre style="white-space: pre-wrap; margin: 0; font-size: 14px; color: #e0e0e0;">{message}</pre>
                </div>
                <p style="color: #666; font-size: 12px; margin-top: 16px;">Sent by Portainer Backup Companion</p>
            </div>
            """
            msg.attach(MIMEText(message, "plain"))
            msg.attach(MIMEText(html_body, "html"))

            smtp_host = email_cfg.get("smtp_host", "")
            smtp_port = int(email_cfg.get("smtp_port", 587))
            smtp_user = email_cfg.get("smtp_user", "")
            smtp_password = email_cfg.get("smtp_password", "")
            use_tls = email_cfg.get("smtp_use_tls", True)

            with smtplib.SMTP(smtp_host, smtp_port) as server:
                if use_tls:
                    server.starttls()
                if smtp_user:
                    server.login(smtp_user, smtp_password)
                server.send_message(msg)
            logger.info("Email notification sent")
        except Exception as e:
            logger.error(f"Failed to send Email notification: {e}")

    async def _send_telegram(self, message: str, telegram_cfg: dict):
        """Send notification via Telegram Bot API."""
        try:
            bot_token = telegram_cfg["bot_token"]
            chat_id = telegram_cfg["chat_id"]
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json={
                    "chat_id": chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                })
                if resp.status_code != 200:
                    logger.error(f"Telegram API error: {resp.status_code} — {resp.text}")
                else:
                    logger.info("Telegram notification sent")
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")

    async def _send_webhook(self, job: BackupJob, webhook_url: str):
        try:
            async with httpx.AsyncClient() as client:
                data = {
                    "id": job.id,
                    "stack_name": job.stack_name,
                    "status": job.status,
                    "error_message": job.error_message,
                    "completed_at": job.completed_at.isoformat() if job.completed_at else None
                }
                await client.post(webhook_url, json=data)
            logger.info("Webhook notification sent")
        except Exception as e:
            logger.error(f"Failed to send generic webhook: {e}")

# Singleton
notifier = Notifier()
