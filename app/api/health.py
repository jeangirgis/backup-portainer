import shutil
import logging
import docker
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select, func, desc
from app.db import AsyncSessionLocal
from app.models import BackupJob, Schedule
from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/health", tags=["health"])
settings = get_settings()


@router.get("")
async def get_health(request: Request):
    health = {
        "docker": False,
        "portainer": False,
        "disk_total_gb": 0,
        "disk_free_gb": 0,
        "disk_used_pct": 0,
        "total_backups": 0,
        "total_size_mb": 0,
        "last_backup": None,
        "last_backup_status": None,
        "total_stacks": 0,
        "active_schedules": 0,
    }

    # Docker socket
    try:
        client = docker.from_env()
        client.ping()
        health["docker"] = True
    except Exception:
        pass

    # Portainer API
    ssl_verify = settings.PORTAINER_SSL_VERIFY.lower() == "true"
    try:
        async with httpx.AsyncClient(timeout=5.0, verify=ssl_verify) as client:
            resp = await client.get(
                f"{settings.PORTAINER_URL.rstrip('/')}/api/system/status",
                headers={"X-API-Key": settings.PORTAINER_API_TOKEN},
            )
            health["portainer"] = resp.status_code == 200
            
            resp_stacks = await client.get(
                f"{settings.PORTAINER_URL.rstrip('/')}/api/stacks",
                headers={"X-API-Key": settings.PORTAINER_API_TOKEN},
            )
            if resp_stacks.status_code == 200:
                health["total_stacks"] = len(resp_stacks.json())
    except Exception:
        pass

    # Disk usage
    try:
        usage = shutil.disk_usage(settings.LOCAL_BACKUP_DIR)
        health["disk_total_gb"] = round(usage.total / (1024**3), 1)
        health["disk_free_gb"] = round(usage.free / (1024**3), 1)
        health["disk_used_pct"] = round((usage.used / usage.total) * 100, 1)
    except Exception:
        pass

    # Backup stats
    try:
        async with AsyncSessionLocal() as db:
            count_result = await db.execute(
                select(func.count()).select_from(BackupJob).where(BackupJob.status == "success")
            )
            health["total_backups"] = count_result.scalar() or 0

            size_result = await db.execute(
                select(func.sum(BackupJob.size_bytes)).where(BackupJob.status == "success")
            )
            total_bytes = size_result.scalar() or 0
            health["total_size_mb"] = round(total_bytes / (1024 * 1024), 2)

            last_result = await db.execute(
                select(BackupJob)
                .order_by(desc(BackupJob.created_at))
                .limit(1)
            )
            last = last_result.scalar_one_or_none()
            if last:
                health["last_backup"] = last.created_at.strftime("%b %d, %H:%M")
                health["last_backup_status"] = last.status.upper()
            else:
                health["last_backup"] = "Never"
                
            sch_result = await db.execute(select(func.count(Schedule.id)).where(Schedule.enabled == True))
            health["active_schedules"] = sch_result.scalar() or 0
    except Exception:
        pass

    if "hx-request" in request.headers:
        disk_color = "var(--success)" if health["disk_used_pct"] < 80 else "var(--warning)" if health["disk_used_pct"] < 95 else "var(--error)"
        
        status_color = "var(--text)"
        if health["last_backup_status"] == "SUCCESS":
            status_color = "var(--success)"
        elif health["last_backup_status"] == "FAILED":
            status_color = "var(--error)"

        last_backup_html = f'<span style="color: {status_color}; font-weight: 600;">{health["last_backup_status"] or "No backups"}</span> <span style="font-size: 0.8rem; color: var(--text-muted); display: block; margin-top: 0.2rem;">{health["last_backup"]}</span>'

        gb_used = health["total_size_mb"] / 1024

        html = f"""
        <div class="summary-grid">
            <div class="summary-card">
                <div class="summary-icon">📦</div>
                <div class="summary-info">
                    <span class="summary-label">Protected Stacks</span>
                    <span class="summary-value">{health['total_stacks']}</span>
                </div>
            </div>
            <div class="summary-card">
                <div class="summary-icon">💾</div>
                <div class="summary-info">
                    <span class="summary-label">Storage Used</span>
                    <span class="summary-value" title="{health['total_backups']} total backups">{gb_used:.2f} GB</span>
                </div>
            </div>
            <div class="summary-card">
                <div class="summary-icon">⏱️</div>
                <div class="summary-info">
                    <span class="summary-label">Active Schedules</span>
                    <span class="summary-value">{health['active_schedules']}</span>
                </div>
            </div>
            <div class="summary-card">
                <div class="summary-icon">⚡</div>
                <div class="summary-info">
                    <span class="summary-label">Last Backup</span>
                    <span class="summary-value" style="font-size: 0.9rem; line-height: 1.1;">{last_backup_html}</span>
                </div>
            </div>
            <div class="summary-card">
                <div class="summary-icon">💽</div>
                <div class="summary-info">
                    <span class="summary-label">Host Disk Used</span>
                    <span class="summary-value" style="color: {disk_color}">{health['disk_used_pct']}%</span>
                </div>
            </div>
        </div>
        """
        return HTMLResponse(content=html)

    return health
