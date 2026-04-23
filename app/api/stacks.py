from fastapi import APIRouter, Depends, HTTPException, Request
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime
import httpx
from app.config import get_settings

router = APIRouter(prefix="/stacks", tags=["stacks"])
settings = get_settings()

class StackInfo(BaseModel):
    id: str
    name: str
    status: str
    volume_count: int = 0
    last_backup_at: Optional[datetime] = None

from fastapi.responses import HTMLResponse

@router.get("", response_model=List[StackInfo])
async def list_stacks(request: Request):
    # Debug: Print SSL verify setting
    ssl_verify = settings.PORTAINER_SSL_VERIFY.lower() == "true"
    print(f"DEBUG: PORTAINER_SSL_VERIFY raw='{settings.PORTAINER_SSL_VERIFY}', calculated={ssl_verify}")
    
    async with httpx.AsyncClient(timeout=10.0, verify=ssl_verify) as client:
        try:
            resp = await client.get(
                f"{settings.PORTAINER_URL.rstrip('/')}/api/stacks",
                headers={"X-API-Key": settings.PORTAINER_API_TOKEN}
            )
            resp.raise_for_status()
            stacks = resp.json()
            
            stack_list = [
                StackInfo(
                    id=str(s["Id"]),
                    name=s["Name"],
                    status="running" if s["Status"] == 1 else "stopped"
                ) for s in stacks
            ]

            if "hx-request" in request.headers:
                html = ""
                for s in stack_list:
                    status_class = "status-running" if s.status == "running" else "status-stopped"
                    html += f"""
                    <div class="card">
                        <div style="display: flex; justify-content: space-between; margin-bottom: 1rem;">
                            <span class="status-badge {status_class}">{s.status.upper()}</span>
                        </div>
                        <h3 style="margin-bottom: 0.5rem;">{s.name}</h3>
                        <p style="color: var(--text-muted); font-size: 0.875rem; margin-bottom: 1.5rem;">ID: {s.id}</p>
                        <div id="stack-actions-{s.id}">
                            <button class="btn btn-primary" 
                                    hx-post="/api/backups/{s.id}" 
                                    hx-target="#stack-actions-{s.id}"
                                    hx-indicator="#spinner-{s.id}">
                                <div id="spinner-{s.id}" class="spinner htmx-indicator" style="width: 1rem; height: 1rem;"></div>
                                Backup Now
                            </button>
                        </div>
                    </div>
                    """
                return HTMLResponse(content=html)
            
            return stack_list
        except Exception as e:
            if "hx-request" in request.headers:
                return HTMLResponse(content=f'<div class="card" style="border-color: var(--error); color: var(--error);">Error: {str(e)}</div>')
            raise HTTPException(status_code=500, detail=f"Failed to fetch stacks: {e}")

@router.get("/{stack_id}", response_model=StackInfo)
async def get_stack(stack_id: str):
    async with httpx.AsyncClient(timeout=10.0, verify=(settings.PORTAINER_SSL_VERIFY.lower() == "true")) as client:
        try:
            resp = await client.get(
                f"{settings.PORTAINER_URL.rstrip('/')}/api/stacks/{stack_id}",
                headers={"X-API-Key": settings.PORTAINER_API_TOKEN}
            )
            resp.raise_for_status()
            s = resp.json()
            return StackInfo(
                id=str(s["Id"]),
                name=s["Name"],
                status="running" if s["Status"] == 1 else "stopped"
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to fetch stack: {e}")
