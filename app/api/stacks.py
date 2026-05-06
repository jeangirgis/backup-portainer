from fastapi import APIRouter, Depends, HTTPException, Request
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime
import httpx
import docker
import logging
from app.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stacks", tags=["stacks"])
settings = get_settings()


class StackInfo(BaseModel):
    id: str
    name: str
    status: str
    volume_count: int = 0
    container_count: int = 0
    last_backup_at: Optional[datetime] = None


from fastapi.responses import HTMLResponse


def _get_stack_stats(stack_name: str) -> dict:
    """Get volume and container count for a stack from Docker."""
    stats = {"volumes": 0, "containers": 0}
    try:
        client = docker.from_env()
        normalized = stack_name.lower()
        
        containers = client.containers.list(
            all=True, filters={"label": f"com.docker.compose.project={normalized}"}
        )
        stats["containers"] = len(containers)
        
        vol_names = set()
        for c in containers:
            for mount in c.attrs.get("Mounts", []):
                if mount.get("Type") == "volume" and "Name" in mount:
                    vol_names.add(mount["Name"])
        stats["volumes"] = len(vol_names)
    except Exception as e:
        logger.debug(f"Could not get stack stats for {stack_name}: {e}")
    return stats


@router.get("", response_model=List[StackInfo])
async def list_stacks(request: Request):
    ssl_verify = settings.PORTAINER_SSL_VERIFY.lower() == "true"
    
    async with httpx.AsyncClient(timeout=10.0, verify=ssl_verify) as client:
        try:
            resp = await client.get(
                f"{settings.PORTAINER_URL.rstrip('/')}/api/stacks",
                headers={"X-API-Key": settings.PORTAINER_API_TOKEN}
            )
            resp.raise_for_status()
            stacks = resp.json()
            
            stack_list = []
            for s in stacks:
                name = s["Name"]
                stats = _get_stack_stats(name)
                stack_list.append(StackInfo(
                    id=str(s["Id"]),
                    name=name,
                    status="running" if s["Status"] == 1 else "stopped",
                    volume_count=stats["volumes"],
                    container_count=stats["containers"],
                ))

            if "hx-request" in request.headers:
                if not stack_list:
                    return HTMLResponse(content="""
                    <div class="card" style="grid-column: 1/-1; text-align: center; padding: 4rem 1rem;">
                        <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="color: var(--text-muted); opacity: 0.3; margin-bottom: 1rem;">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M21 7.5l-9-5.25L3 7.5m18 0l-9 5.25m9-5.25v9l-9 5.25M3 7.5l9 5.25M3 7.5v9l9 5.25m0-9v9" />
                        </svg>
                        <h3 style="font-family: 'Outfit', sans-serif; font-size: 1.2rem; color: var(--text); margin-bottom: 0.5rem;">No Stacks Found</h3>
                        <p style="color: var(--text-muted); font-size: 0.9rem;">There are no Docker stacks available in Portainer to backup.</p>
                    </div>
                    """)

                html = ""
                for s in stack_list:
                    status_class = "status-running" if s.status == "running" else "status-stopped"
                    html += f"""
                    <div class="card stack-card">
                        <div class="stack-card-grid">
                            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem;">
                                <span class="status-badge {status_class}">{s.status.upper()}</span>
                                <span style="font-size: 0.75rem; color: var(--text-muted);">ID: {s.id}</span>
                            </div>
                            <h3 style="margin-bottom: 0.75rem; font-size: 1.25rem;">{s.name}</h3>
                            <div class="stack-meta">
                                <span>📦 {s.container_count} container{'s' if s.container_count != 1 else ''}</span>
                                <span>💾 {s.volume_count} volume{'s' if s.volume_count != 1 else ''}</span>
                            </div>
                            <div id="stack-actions-{s.id}" style="margin-top: 1rem;">
                                <button class="btn btn-primary" style="width: 100%;"
                                        hx-post="/api/backups/{s.id}" 
                                        hx-target="#stack-actions-{s.id}"
                                        hx-indicator="#spinner-{s.id}">
                                    <div id="spinner-{s.id}" class="spinner htmx-indicator" style="width: 1.2rem; height: 1.2rem; border-width: 2px;"></div>
                                    <span>Backup Now</span>
                                </button>
                            </div>
                        </div>
                        <div class="stack-card-list">
                            <div class="stack-list-info">
                                <span class="status-badge {status_class}" style="padding: 0.25rem 0.5rem; font-size: 0.65rem;">{s.status.upper()}</span>
                                <strong style="font-size: 1rem;">{s.name}</strong>
                                <span style="font-size: 0.7rem; color: var(--text-muted);">ID: {s.id}</span>
                            </div>
                            <div class="stack-list-meta">
                                <span>📦 {s.container_count}</span>
                                <span>💾 {s.volume_count}</span>
                            </div>
                            <div id="stack-actions-list-{s.id}" class="stack-list-action">
                                <button class="btn btn-primary btn-sm"
                                        hx-post="/api/backups/{s.id}" 
                                        hx-target="#stack-actions-list-{s.id}"
                                        hx-indicator="#spinner-list-{s.id}">
                                    <div id="spinner-list-{s.id}" class="spinner htmx-indicator" style="width: 1rem; height: 1rem; border-width: 2px;"></div>
                                    <span>Backup Now</span>
                                </button>
                            </div>
                        </div>
                    </div>
                    """
                return HTMLResponse(content=html)
            
            return stack_list
        except Exception as e:
            if "hx-request" in request.headers:
                return HTMLResponse(content=f"""
                    <div class="card" style="grid-column: 1/-1; border-color: var(--error);">
                        <h3 style="color: var(--error); margin-bottom: 0.5rem;">Connection Error</h3>
                        <p style="color: var(--text-muted); font-size: 0.875rem;">{str(e)}</p>
                        <button class="btn btn-outline" style="margin-top: 1rem;"
                                hx-get="/api/stacks" hx-target="#stack-grid" hx-swap="innerHTML">
                            Retry Connection
                        </button>
                    </div>
                """)
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
            stats = _get_stack_stats(s["Name"])
            return StackInfo(
                id=str(s["Id"]),
                name=s["Name"],
                status="running" if s["Status"] == 1 else "stopped",
                volume_count=stats["volumes"],
                container_count=stats["containers"],
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to fetch stack: {e}")
