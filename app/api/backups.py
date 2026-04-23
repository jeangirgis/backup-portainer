from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from typing import List
from pydantic import BaseModel
from datetime import datetime
from pathlib import Path
from app.db import get_db
from app.models import BackupJob
from app.engine.engine import BackupEngine
from app.config import get_settings

router = APIRouter(prefix="/backups", tags=["backups"])
engine = BackupEngine()
settings = get_settings()

class BackupJobSchema(BaseModel):
    id: str
    stack_id: str
    stack_name: str
    status: str
    storage_path: str | None
    size_bytes: int | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None
    triggered_by: str

@router.post("/{stack_id}", response_model=BackupJobSchema)
async def start_backup(stack_id: str, background_tasks: BackgroundTasks, request: Request):
    job = await engine.create_job(stack_id)
    background_tasks.add_task(engine.run_job, job.id)
    
    if "hx-request" in request.headers:
        return HTMLResponse(content=f"""
            <div hx-get="/api/backups/{job.id}/status" hx-trigger="load, every 2s" hx-swap="outerHTML">
                <div class="status-badge status-pending">PENDING</div>
                <div class="spinner" style="width: 1rem; height: 1rem; margin-top: 0.5rem;"></div>
            </div>
        """)
    return job

@router.get("", response_model=List[BackupJobSchema])
async def list_backups(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(BackupJob).order_by(desc(BackupJob.created_at)).limit(50))
    jobs = result.scalars().all()
    
    if "hx-request" in request.headers:
        html = ""
        for job in jobs:
            size_mb = f"{job.size_bytes / (1024*1024):.2f} MB" if job.size_bytes else "-"
            date_str = job.created_at.strftime("%Y-%m-%d %H:%M")
            status_class = f"status-{job.status}"
            html += f"""
            <tr id="job-{job.id}">
                <td><strong>{job.stack_name}</strong></td>
                <td><span class="status-badge {status_class}">{job.status.upper()}</span></td>
                <td>{size_mb}</td>
                <td style="color: var(--text-muted);">{date_str}</td>
                <td>
                        <div style="display: flex; gap: 0.5rem;">
                            {f'<a href="/api/backups/{job.id}/download?token={settings.SECRET_KEY}" class="btn btn-outline" style="padding: 0.25rem 0.5rem; font-size: 0.75rem;">Download</a>' if job.status == 'success' else ''}
                            {f'<button class="btn btn-primary" style="padding: 0.25rem 0.5rem; font-size: 0.75rem;" hx-post="/api/backups/{job.id}/restore" hx-indicator="#job-spinner-{job.id}" hx-confirm="This will overwrite current data. Proceed?">Restore</button>' if job.status == 'success' else ''}
                            <button class="btn btn-outline" style="padding: 0.25rem 0.5rem; font-size: 0.75rem; color: var(--error);"
                                    hx-delete="/api/backups/{job.id}" hx-target="#job-{job.id}" hx-swap="outerHTML" hx-confirm="Delete this backup?">
                                Delete
                            </button>
                            <div id="job-spinner-{job.id}" class="spinner htmx-indicator" style="width: 1rem; height: 1rem;"></div>
                        </div>
                </td>
            </tr>
            """
        if not html:
            html = '<tr><td colspan="5" style="text-align: center; padding: 2rem; color: var(--text-muted);">No backups found.</td></tr>'
        return HTMLResponse(content=html)
    
    return jobs

@router.get("/{job_id}/status", response_model=BackupJobSchema)
async def get_backup_status(job_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(BackupJob).where(BackupJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if "hx-request" in request.headers:
        if job.status in ["pending", "running"]:
            return HTMLResponse(content=f"""
                <div hx-get="/api/backups/{job.id}/status" hx-trigger="every 2s" hx-swap="outerHTML">
                    <span class="status-badge status-{job.status}">{job.status.upper()}</span>
                    <div class="spinner" style="width: 1rem; height: 1rem; margin-top: 0.5rem;"></div>
                </div>
            """)
        elif job.status == "success":
            return HTMLResponse(content=f"""
                <div>
                    <span class="status-badge status-success">SUCCESS</span>
                    <p style="font-size: 0.75rem; color: var(--text-muted); margin-top: 0.5rem;">
                        {job.completed_at.strftime('%Y-%m-%d %H:%M:%S') if job.completed_at else ''}
                    </p>
                </div>
            """)
        else:
            return HTMLResponse(content=f"""
                <div>
                    <span class="status-badge status-failed">FAILED</span>
                    <p style="font-size: 0.75rem; color: var(--error); margin-top: 0.5rem;">{job.error_message}</p>
                    <button class="btn btn-outline" style="margin-top: 0.5rem; width: 100%;" 
                            hx-post="/api/backups/{job.stack_id}" hx-target="closest div" hx-swap="outerHTML">
                        Retry
                    </button>
                </div>
            """)
            
    return job

@router.get("/{job_id}/download")
async def download_backup(job_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(BackupJob).where(BackupJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job or not job.storage_path:
        raise HTTPException(status_code=404, detail="Backup not found")
    
    # Only works for local storage for now
    file_path = Path(settings.LOCAL_BACKUP_DIR) / job.storage_path
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")
    
    return FileResponse(file_path, filename=job.storage_path)

@router.delete("/{job_id}")
async def delete_backup(job_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(BackupJob).where(BackupJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Delete file
    driver = settings.get_storage_driver()
    if job.storage_path:
        await driver.delete(job.storage_path)
    
    # Delete record
    await db.delete(job)
    await db.commit()
    return HTMLResponse(content="")

from app.engine.restore import RestoreEngine
restore_engine = RestoreEngine()

@router.post("/{job_id}/restore")
async def restore_backup(job_id: str, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(BackupJob).where(BackupJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job or not job.storage_path:
        raise HTTPException(status_code=404, detail="Backup not found")
    
    # Run restore in background
    file_path = Path(settings.LOCAL_BACKUP_DIR) / job.storage_path
    background_tasks.add_task(restore_engine.restore, file_path)
    
    return HTMLResponse(content='<span style="color: var(--success); font-size: 0.75rem;">Restore Started...</span>')
