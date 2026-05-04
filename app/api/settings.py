from fastapi import APIRouter, Request, HTTPException, Form
from fastapi.responses import HTMLResponse
import json
import smtplib
from email.mime.text import MIMEText
from pathlib import Path
import httpx
import logging
from app.config import get_settings, load_runtime_config, save_runtime_config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/settings", tags=["settings"])
settings = get_settings()


@router.get("")
async def get_settings_info(request: Request):
    notif = settings.get_effective_notification_config()
    storage = settings.get_effective_storage_config()

    info = {
        "PORTAINER_URL": settings.PORTAINER_URL,
        "STORAGE_BACKEND": storage["backend"],
        "NOTIFICATIONS": {
            "slack": notif["slack"]["enabled"],
            "email": notif["email"]["enabled"],
            "telegram": notif["telegram"]["enabled"],
            "webhook": notif["webhook"]["enabled"],
        }
    }

    if "hx-request" in request.headers:
        html = f"""
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem;">
            <div>
                <p style="font-size: 0.75rem; color: var(--text-muted);">Portainer URL</p>
                <p style="font-weight: 500;">{info['PORTAINER_URL']}</p>
            </div>
            <div>
                <p style="font-size: 0.75rem; color: var(--text-muted);">Storage Backend</p>
                <p style="font-weight: 500; text-transform: uppercase;">{info['STORAGE_BACKEND']}</p>
            </div>
            <div>
                <p style="font-size: 0.75rem; color: var(--text-muted);">Notifications</p>
                <div style="display: flex; gap: 0.5rem; margin-top: 0.25rem; flex-wrap: wrap;">
                    <span class="status-badge" style="background: { 'rgba(34, 197, 94, 0.2)' if info['NOTIFICATIONS']['email'] else 'rgba(148, 163, 184, 0.2)' }; color: { '#4ade80' if info['NOTIFICATIONS']['email'] else '#94a3b8' };">Email</span>
                    <span class="status-badge" style="background: { 'rgba(34, 197, 94, 0.2)' if info['NOTIFICATIONS']['telegram'] else 'rgba(148, 163, 184, 0.2)' }; color: { '#4ade80' if info['NOTIFICATIONS']['telegram'] else '#94a3b8' };">Telegram</span>
                    <span class="status-badge" style="background: { 'rgba(34, 197, 94, 0.2)' if info['NOTIFICATIONS']['slack'] else 'rgba(148, 163, 184, 0.2)' }; color: { '#4ade80' if info['NOTIFICATIONS']['slack'] else '#94a3b8' };">Slack</span>
                    <span class="status-badge" style="background: { 'rgba(34, 197, 94, 0.2)' if info['NOTIFICATIONS']['webhook'] else 'rgba(148, 163, 184, 0.2)' }; color: { '#4ade80' if info['NOTIFICATIONS']['webhook'] else '#94a3b8' };">Webhook</span>
                </div>
            </div>
        </div>
        """
        return HTMLResponse(content=html)

    return info


@router.post("/test")
async def test_connection(request: Request):
    async with httpx.AsyncClient(timeout=5.0, verify=(settings.PORTAINER_SSL_VERIFY.lower() == "true")) as client:
        try:
            resp = await client.get(
                f"{settings.PORTAINER_URL.rstrip('/')}/api/system/status",
                headers={"X-API-Key": settings.PORTAINER_API_TOKEN}
            )
            resp.raise_for_status()

            if "hx-request" in request.headers:
                return HTMLResponse(content='<p style="color: var(--success); font-weight: 600;">✅ Connection successful!</p>')
            return {"status": "ok"}
        except Exception as e:
            if "hx-request" in request.headers:
                return HTMLResponse(content=f'<p style="color: var(--error); font-weight: 600;">❌ Connection failed: {str(e)}</p>')
            raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────
# Storage Configuration
# ──────────────────────────────────────────────

@router.get("/storage/current")
async def get_storage_config():
    """Return current effective storage configuration."""
    return settings.get_effective_storage_config()


@router.post("/storage")
async def save_storage_config(request: Request):
    """Save storage backend configuration."""
    try:
        body = await request.json()
        backend = body.get("backend", "local")
        config = body.get("config", {})

        rc = load_runtime_config()
        if "storage" not in rc:
            rc["storage"] = {}
        rc["storage"]["backend"] = backend
        rc["storage"][backend] = config

        # If Google Drive, also save credentials file
        if backend == "gdrive" and config.get("credentials_json"):
            creds_json = config["credentials_json"]
            # Validate JSON
            json.loads(creds_json)
            base_dir = Path(settings.LOCAL_BACKUP_DIR)
            base_dir.mkdir(parents=True, exist_ok=True)
            creds_path = base_dir / "gdrive_credentials.json"
            config_path = base_dir / "gdrive_config.json"

            with open(creds_path, "w", encoding="utf-8") as f:
                f.write(creds_json)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump({
                    "folder_id": config.get("folder_id", "").strip(),
                    "credentials_path": str(creds_path)
                }, f)

            # Remove credentials_json from runtime config (it's a file)
            rc["storage"][backend].pop("credentials_json", None)

        save_runtime_config(rc)
        return {"status": "ok", "message": f"Storage set to {backend}"}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in credentials")
    except Exception as e:
        logger.error(f"Failed to save storage config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/storage/test")
async def test_storage(request: Request):
    """Test connection to the currently configured storage backend."""
    try:
        body = await request.json()
        backend = body.get("backend", "local")
        config = body.get("config", {})

        if backend == "local":
            test_dir = Path(config.get("backup_dir", settings.LOCAL_BACKUP_DIR))
            test_dir.mkdir(parents=True, exist_ok=True)
            # Write and delete a test file
            test_file = test_dir / ".connection_test"
            test_file.write_text("test")
            test_file.unlink()
            return {"status": "ok", "message": "Local storage is accessible"}

        elif backend == "s3":
            import boto3
            s3_client = boto3.client(
                "s3",
                aws_access_key_id=config.get("access_key"),
                aws_secret_access_key=config.get("secret_key"),
                endpoint_url=config.get("endpoint_url") or None,
                region_name=config.get("region", "us-east-1"),
            )
            s3_client.head_bucket(Bucket=config.get("bucket"))
            return {"status": "ok", "message": f"S3 bucket '{config.get('bucket')}' is accessible"}

        elif backend == "sftp":
            import paramiko
            transport = paramiko.Transport((config.get("host"), int(config.get("port", 22))))
            try:
                if config.get("key_path"):
                    key = paramiko.RSAKey.from_private_key_file(config["key_path"])
                    transport.connect(username=config.get("user"), pkey=key)
                else:
                    transport.connect(username=config.get("user"), password=config.get("password"))
                sftp = paramiko.SFTPClient.from_transport(transport)
                sftp.listdir(config.get("remote_dir", "/backups"))
                sftp.close()
                return {"status": "ok", "message": f"SFTP connection successful"}
            finally:
                transport.close()

        elif backend == "gdrive":
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            creds_path = Path(settings.LOCAL_BACKUP_DIR) / "gdrive_credentials.json"
            if config.get("credentials_json"):
                # Use provided JSON directly
                creds_data = json.loads(config["credentials_json"])
                creds = service_account.Credentials.from_service_account_info(
                    creds_data, scopes=["https://www.googleapis.com/auth/drive"]
                )
            elif creds_path.exists():
                creds = service_account.Credentials.from_service_account_file(
                    str(creds_path), scopes=["https://www.googleapis.com/auth/drive"]
                )
            else:
                return {"status": "error", "message": "No credentials found. Please save credentials first."}

            service = build("drive", "v3", credentials=creds, cache_discovery=False)
            folder_id = config.get("folder_id") or settings.GDRIVE_FOLDER_ID
            if not folder_id:
                return {"status": "error", "message": "No folder ID configured"}
            
            # Try to get the folder itself to verify access
            try:
                folder = service.files().get(
                    fileId=folder_id,
                    fields="id, name",
                    supportsAllDrives=True
                ).execute()
                return {"status": "ok", "message": f"Google Drive folder '{folder.get('name', 'accessible')}' is ready"}
            except Exception as e:
                return {"status": "error", "message": f"Cannot access folder ID. Did you share it with the service account email? Error: {e}"}

        return {"status": "error", "message": f"Unknown backend: {backend}"}

    except Exception as e:
        logger.error(f"Storage test failed: {e}")
        return {"status": "error", "message": str(e)}


# ──────────────────────────────────────────────
# Notification Configuration
# ──────────────────────────────────────────────

@router.get("/notifications/current")
async def get_notification_config():
    """Return current effective notification configuration."""
    config = settings.get_effective_notification_config()
    # Mask sensitive fields
    safe = json.loads(json.dumps(config))
    for channel in safe.values():
        for key in list(channel.keys()):
            if "password" in key or "token" in key or "secret" in key:
                val = channel[key]
                if val and isinstance(val, str) and len(val) > 4:
                    channel[key + "_masked"] = val[:4] + "•" * (len(val) - 4)
    return safe


@router.post("/notifications")
async def save_notification_config(request: Request):
    """Save all notification channel configurations."""
    try:
        body = await request.json()
        rc = load_runtime_config()
        rc["notifications"] = body
        save_runtime_config(rc)
        return {"status": "ok", "message": "Notification settings saved"}
    except Exception as e:
        logger.error(f"Failed to save notification config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/notifications/test")
async def test_notification(request: Request):
    """Send a test notification through a specific channel."""
    try:
        body = await request.json()
        channel = body.get("channel", "")
        config = body.get("config", {})
        test_message = "🧪 Test notification from Backtainer!\nIf you see this, your notification channel is configured correctly."

        if channel == "email":
            try:
                msg = MIMEText(test_message)
                msg['Subject'] = "Backtainer — Test Notification"
                msg['From'] = config.get("from_address", "")
                msg['To'] = config.get("to_address", "")

                smtp_host = config.get("smtp_host", "")
                smtp_port = int(config.get("smtp_port", 587))
                use_tls = config.get("smtp_use_tls", True)

                with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
                    if use_tls:
                        server.starttls()
                    if config.get("smtp_user"):
                        server.login(config["smtp_user"], config.get("smtp_password", ""))
                    server.send_message(msg)
                return {"status": "ok", "message": "Test email sent successfully!"}
            except Exception as e:
                return {"status": "error", "message": f"Email failed: {str(e)}"}

        elif channel == "telegram":
            try:
                bot_token = config.get("bot_token", "")
                chat_id = config.get("chat_id", "")
                url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(url, json={
                        "chat_id": chat_id,
                        "text": test_message,
                        "parse_mode": "HTML",
                    })
                    if resp.status_code == 200:
                        return {"status": "ok", "message": "Test Telegram message sent!"}
                    else:
                        error_data = resp.json()
                        return {"status": "error", "message": f"Telegram API error: {error_data.get('description', resp.text)}"}
            except Exception as e:
                return {"status": "error", "message": f"Telegram failed: {str(e)}"}

        elif channel == "slack":
            try:
                webhook_url = config.get("webhook_url", "")
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(webhook_url, json={"text": test_message})
                    if resp.status_code == 200:
                        return {"status": "ok", "message": "Test Slack message sent!"}
                    else:
                        return {"status": "error", "message": f"Slack error: {resp.status_code} — {resp.text}"}
            except Exception as e:
                return {"status": "error", "message": f"Slack failed: {str(e)}"}

        elif channel == "webhook":
            try:
                url = config.get("url", "")
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(url, json={
                        "event": "test",
                        "message": test_message,
                        "source": "portainer-backup-companion"
                    })
                    return {"status": "ok", "message": f"Webhook responded with {resp.status_code}"}
            except Exception as e:
                return {"status": "error", "message": f"Webhook failed: {str(e)}"}

        return {"status": "error", "message": f"Unknown channel: {channel}"}

    except Exception as e:
        logger.error(f"Notification test failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────
# Legacy Google Drive endpoint (backward compat)
# ──────────────────────────────────────────────

@router.post("/gdrive")
async def save_gdrive_config(request: Request, folder_id: str = Form(...), credentials_json: str = Form(...)):
    try:
        # Validate that it's valid JSON
        json.loads(credentials_json)

        base_dir = Path(settings.LOCAL_BACKUP_DIR)
        base_dir.mkdir(parents=True, exist_ok=True)

        creds_path = base_dir / "gdrive_credentials.json"
        config_path = base_dir / "gdrive_config.json"

        with open(creds_path, "w", encoding="utf-8") as f:
            f.write(credentials_json)

        with open(config_path, "w", encoding="utf-8") as f:
            json.dump({
                "folder_id": folder_id.strip(),
                "credentials_path": str(creds_path)
            }, f)

        msg = "✅ Google Drive configuration saved successfully!"
        return HTMLResponse(content=f'<div class="status-badge status-success" style="margin-top: 1rem; width: 100%; text-align: center;">{msg}</div>')
    except json.JSONDecodeError:
        msg = "❌ Invalid JSON provided for credentials."
        return HTMLResponse(content=f'<div class="status-badge status-failed" style="margin-top: 1rem; width: 100%; text-align: center;">{msg}</div>')
    except Exception as e:
        msg = f"❌ Error saving configuration: {e}"
        return HTMLResponse(content=f'<div class="status-badge status-failed" style="margin-top: 1rem; width: 100%; text-align: center;">{msg}</div>')
