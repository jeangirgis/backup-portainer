from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse
import httpx
from app.config import get_settings

router = APIRouter(prefix="/settings", tags=["settings"])
settings = get_settings()

@router.get("")
async def get_settings_info(request: Request):
    info = {
        "PORTAINER_URL": settings.PORTAINER_URL,
        "STORAGE_BACKEND": settings.STORAGE_BACKEND,
        "NOTIFICATIONS": {
            "slack": bool(settings.NOTIFY_SLACK_WEBHOOK),
            "email": bool(settings.NOTIFY_EMAIL_TO),
            "webhook": bool(settings.NOTIFY_WEBHOOK_URL)
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
                <div style="display: flex; gap: 0.5rem; margin-top: 0.25rem;">
                    <span class="status-badge" style="background: { 'rgba(34, 197, 94, 0.2)' if info['NOTIFICATIONS']['slack'] else 'rgba(148, 163, 184, 0.2)' }; color: { '#4ade80' if info['NOTIFICATIONS']['slack'] else '#94a3b8' };">Slack</span>
                    <span class="status-badge" style="background: { 'rgba(34, 197, 94, 0.2)' if info['NOTIFICATIONS']['email'] else 'rgba(148, 163, 184, 0.2)' }; color: { '#4ade80' if info['NOTIFICATIONS']['email'] else '#94a3b8' };">Email</span>
                    <span class="status-badge" style="background: { 'rgba(34, 197, 94, 0.2)' if info['NOTIFICATIONS']['webhook'] else 'rgba(148, 163, 184, 0.2)' }; color: { '#4ade80' if info['NOTIFICATIONS']['webhook'] else '#94a3b8' };">Webhook</span>
                </div>
            </div>
        </div>
        """
        return HTMLResponse(content=html)
    
    return info

@router.post("/test")
async def test_connection(request: Request):
    async with httpx.AsyncClient(timeout=5.0) as client:
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
