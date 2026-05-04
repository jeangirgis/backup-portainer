from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi import UploadFile, File
import shutil
import uuid
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
from app.engine.restore import RestoreEngine
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/backups", tags=["backups"])
engine = BackupEngine()
settings = get_settings()
restore_engine = RestoreEngine()


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


@router.post("/upload")
async def upload_backup_file(file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    """Accept a .tar.gz file, inspect it, save to storage, and add to db."""
    if not file.filename.endswith(".tar.gz"):
        return HTMLResponse(content="""
            <div class="toast toast-error"><span>❌</span> File must be a .tar.gz backup bundle.</div>
        """)

    # 1. Save locally first
    local_path = Path(settings.LOCAL_BACKUP_DIR) / file.filename
    with open(local_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    size_bytes = local_path.stat().st_size

    # 2. Inspect manifest to get stack info
    from app.engine.restore import inspect_backup
    import asyncio
    loop = asyncio.get_event_loop()
    
    try:
        info = await loop.run_in_executor(None, inspect_backup, local_path)
        manifest = info.get("manifest", {})
        stack_info = manifest.get("stack", {})
        stack_id = stack_info.get("id", "uploaded")
        stack_name = stack_info.get("name", "Unknown (Uploaded)")
    except Exception as e:
        logger.error(f"Failed to inspect uploaded backup: {e}")
        stack_id = "uploaded"
        stack_name = "Unknown (Uploaded)"

    # 3. Upload to configured storage backend
    driver = settings.get_storage_driver()
    try:
        storage_path = await driver.upload(local_path, file.filename)
    except Exception as e:
        logger.error(f"Failed to upload to storage backend: {e}")
        # Even if storage upload fails, we keep the local file and record it if local is the backend
        storage_path = file.filename if settings.STORAGE_BACKEND == "local" else None
        
        if not storage_path:
            # If not local and it failed to upload to remote, we should probably fail
            return HTMLResponse(content=f"""
                <div class="toast toast-error"><span>❌</span> Failed to save to storage backend: {e}</div>
            """)

    # 4. Save to Database
    job_id = str(uuid.uuid4())
    job = BackupJob(
        id=job_id,
        stack_id=stack_id,
        stack_name=stack_name,
        status="success",
        storage_path=storage_path,
        size_bytes=size_bytes,
        triggered_by="manual (uploaded)",
        created_at=datetime.utcnow(),
        completed_at=datetime.utcnow()
    )
    db.add(job)
    await db.commit()
    
    # Trigger refresh
    return HTMLResponse(content="""
        <div class="toast toast-success"><span>✅</span> Backup uploaded successfully! Refreshing...</div>
        <script>setTimeout(() => htmx.ajax('GET', '/api/backups', '#backup-list'), 1500);</script>
    """)


@router.post("/{stack_id}", response_model=BackupJobSchema)
async def start_backup(stack_id: str, background_tasks: BackgroundTasks, request: Request):
    job = await engine.create_job(stack_id)
    background_tasks.add_task(engine.run_job, job.id)
    
    if "hx-request" in request.headers:
        return HTMLResponse(content=f"""
            <div hx-get="/api/backups/{job.id}/status" hx-trigger="load, every 2s" hx-swap="outerHTML"
                 style="display: flex; flex-direction: column; align-items: center; gap: 0.5rem;">
                <div class="status-badge status-pending">PENDING</div>
                <div class="spinner"></div>
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
            
            # Triggered by display
            trigger = job.triggered_by
            if trigger == "manual":
                trigger_html = '<span class="trigger-badge trigger-manual">Manual</span>'
            elif trigger.startswith("schedule:"):
                trigger_html = '<span class="trigger-badge trigger-schedule">Scheduled</span>'
            else:
                trigger_html = f'<span class="trigger-badge">{trigger}</span>'
            
            # Error message tooltip for failed jobs
            error_html = ""
            if job.status == "failed" and job.error_message:
                short_error = job.error_message[:80] + "..." if len(job.error_message) > 80 else job.error_message
                error_html = f'<div class="error-hint">{short_error}</div>'

            html += f"""
            <tr id="job-{job.id}">
                <td>
                    <strong>{job.stack_name}</strong>
                    {error_html}
                </td>
                <td><span class="status-badge {status_class}">{job.status.upper()}</span></td>
                <td>{size_mb}</td>
                <td>{trigger_html}</td>
                <td style="color: var(--text-muted);">{date_str}</td>
                <td>
                    <div style="display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap;">
                        {f'<a href="/api/backups/{job.id}/download?token={settings.SECRET_KEY}" class="btn btn-sm btn-outline">Download</a>' if job.status == 'success' else ''}
                        {f'<button class="btn btn-sm btn-primary" hx-post="/api/backups/{job.id}/restore" hx-target="#restore-toast" hx-swap="innerHTML" hx-confirm="This will overwrite existing volume data. Proceed?">Restore</button>' if job.status == 'success' else ''}
                        <button class="btn btn-sm btn-danger"
                                hx-delete="/api/backups/{job.id}" hx-target="#job-{job.id}" hx-swap="outerHTML" hx-confirm="Delete this backup?">
                            Delete
                        </button>
                    </div>
                </td>
            </tr>
            """
        if not html:
            html = '<tr><td colspan="6" style="text-align: center; padding: 3rem; color: var(--text-muted);">No backups yet. Go to Dashboard and click "Backup Now" on a stack.</td></tr>'
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
                <div hx-get="/api/backups/{job.id}/status" hx-trigger="every 2s" hx-swap="outerHTML"
                     style="display: flex; flex-direction: column; align-items: center; gap: 0.5rem;">
                    <span class="status-badge status-{job.status}">{job.status.upper()}</span>
                    <div class="spinner"></div>
                </div>
            """)
        elif job.status == "success":
            size_mb = f"{job.size_bytes / (1024*1024):.2f} MB" if job.size_bytes else ""
            return HTMLResponse(content=f"""
                <div>
                    <span class="status-badge status-success">SUCCESS</span>
                    <p style="font-size: 0.75rem; color: var(--text-muted); margin-top: 0.5rem;">
                        {size_mb} &mdash; {job.completed_at.strftime('%H:%M:%S') if job.completed_at else ''}
                    </p>
                </div>
            """)
        else:
            short_err = (job.error_message or "Unknown error")[:100]
            return HTMLResponse(content=f"""
                <div>
                    <span class="status-badge status-failed">FAILED</span>
                    <p style="font-size: 0.75rem; color: var(--error); margin-top: 0.5rem;">{short_err}</p>
                    <button class="btn btn-sm btn-outline" style="margin-top: 0.5rem; width: 100%;" 
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


@router.post("/{job_id}/restore")
async def restore_backup(job_id: str, db: AsyncSession = Depends(get_db)):
    """Run restore synchronously and return immediate results."""
    result = await db.execute(select(BackupJob).where(BackupJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job or not job.storage_path:
        return HTMLResponse(content="""
            <div class="toast toast-error"><span>❌</span> Backup record not found.</div>
        """)
    
    file_path = Path(settings.LOCAL_BACKUP_DIR) / job.storage_path
    if not file_path.exists():
        return HTMLResponse(content=f"""
            <div class="toast toast-error">
                <span>❌</span> Backup file not found: <code>{job.storage_path}</code>
            </div>
        """)

    # Run restore synchronously (it uses Docker SDK which is sync)
    import asyncio
    loop = asyncio.get_event_loop()
    try:
        restore_result = await loop.run_in_executor(None, restore_engine.restore, file_path)
    except Exception as e:
        return HTMLResponse(content=f"""
            <div class="toast toast-error">
                <span>❌</span> Restore crashed: {str(e)[:200]}
            </div>
        """)

    # Build result HTML
    status = restore_result.get("status", "failed")
    stack = restore_result.get("stack_name", "unknown")
    restored = restore_result.get("volumes_restored", 0)
    found = restore_result.get("volumes_found", 0)
    error = restore_result.get("error")
    details = restore_result.get("details", [])

    if status == "success":
        details_html = "".join(f"<li>{d}</li>" for d in details)
        return HTMLResponse(content=f"""
            <div class="toast toast-success">
                <div>
                    <strong>✅ Restore Complete — {stack}</strong><br>
                    <span style="font-size: 0.8rem;">{restored}/{found} volumes restored</span>
                    <ul style="margin-top: 0.5rem; font-size: 0.75rem; list-style: none; padding: 0;">{details_html}</ul>
                </div>
            </div>
        """)
    elif status == "partial" or status == "empty":
        details_html = "".join(f"<li>{d}</li>" for d in details)
        return HTMLResponse(content=f"""
            <div class="toast toast-info">
                <div>
                    <strong>⚠️ Partial Restore — {stack}</strong><br>
                    <span style="font-size: 0.8rem;">{restored}/{found} volumes restored</span>
                    {f'<br><span style="font-size: 0.75rem; color: var(--warning);">{error}</span>' if error else ''}
                    <ul style="margin-top: 0.5rem; font-size: 0.75rem; list-style: none; padding: 0;">{details_html}</ul>
                </div>
            </div>
        """)
    else:
        return HTMLResponse(content=f"""
            <div class="toast toast-error">
                <div>
                    <strong>❌ Restore Failed — {stack}</strong><br>
                    <span style="font-size: 0.8rem;">{error or 'Unknown error'}</span>
                </div>
            </div>
        """)


@router.get("/{job_id}/inspect")
async def inspect_backup_endpoint(job_id: str, db: AsyncSession = Depends(get_db)):
    """Inspect what's inside a backup without restoring it."""
    result = await db.execute(select(BackupJob).where(BackupJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job or not job.storage_path:
        raise HTTPException(status_code=404, detail="Backup not found")
    
    file_path = Path(settings.LOCAL_BACKUP_DIR) / job.storage_path
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")
    
    from app.engine.restore import inspect_backup
    import asyncio
    loop = asyncio.get_event_loop()
    info = await loop.run_in_executor(None, inspect_backup, file_path)
    return info





