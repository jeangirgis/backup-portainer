import shutil
import logging
import docker
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select, func
from app.db import AsyncSessionLocal
from app.models import BackupJob
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
                select(BackupJob.completed_at)
                .where(BackupJob.status == "success")
                .order_by(BackupJob.completed_at.desc())
                .limit(1)
            )
            last = last_result.scalar()
            health["last_backup"] = last.strftime("%Y-%m-%d %H:%M") if last else "Never"
    except Exception:
        pass

    if "hx-request" in request.headers:
        docker_icon = "🟢" if health["docker"] else "🔴"
        portainer_icon = "🟢" if health["portainer"] else "🔴"
        disk_color = "var(--success)" if health["disk_used_pct"] < 80 else "var(--warning)" if health["disk_used_pct"] < 95 else "var(--error)"

        html = f"""
        <div class="stats-grid">
            <div class="stat-card">
                <span class="stat-value">{health['total_backups']}</span>
                <span class="stat-label">Total Backups</span>
            </div>
            <div class="stat-card">
                <span class="stat-value">{health['total_size_mb']} MB</span>
                <span class="stat-label">Total Size</span>
            </div>
            <div class="stat-card">
                <span class="stat-value">{health['last_backup'] or 'Never'}</span>
                <span class="stat-label">Last Backup</span>
            </div>
            <div class="stat-card">
                <span class="stat-value" style="color: {disk_color}">{health['disk_used_pct']}%</span>
                <span class="stat-label">Disk ({health['disk_free_gb']} GB free)</span>
            </div>
            <div class="stat-card">
                <span class="stat-value">{docker_icon}</span>
                <span class="stat-label">Docker</span>
            </div>
            <div class="stat-card">
                <span class="stat-value">{portainer_icon}</span>
                <span class="stat-label">Portainer</span>
            </div>
        </div>
        """
        return HTMLResponse(content=html)

    return health
