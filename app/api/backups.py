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
        # For remote backends, download first
        backend = settings.get_effective_storage_backend()
        if backend != "local":
            try:
                driver = settings.get_storage_driver()
                local_filename = f"{job.stack_name}_{job.created_at.strftime('%Y%m%d_%H%M%S')}.tar.gz"
                file_path = Path(settings.LOCAL_BACKUP_DIR) / local_filename
                logger.info(f"Downloading backup from {backend} for download endpoint")
                await driver.download(job.storage_path, file_path)
            except Exception as e:
                logger.error(f"Failed to download from {backend}: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to download from {backend}: {e}")
        else:
            raise HTTPException(status_code=404, detail="File not found on disk")
    
    download_name = f"{job.stack_name}_{job.created_at.strftime('%Y%m%d_%H%M%S')}.tar.gz"
    return FileResponse(file_path, filename=download_name)


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


# ── In-memory progress tracking for restore operations ──
import threading
_restore_progress = {}  # restore_id -> { steps: [...], result: dict|None, error: str|None }
_restore_lock = threading.Lock()

RESTORE_STEPS = [
    {"id": "download", "label": "Download Backup", "icon": "📥"},
    {"id": "unpack",   "label": "Extract Archive",  "icon": "📦"},
    {"id": "stop",     "label": "Stop Stack",        "icon": "⏹️"},
    {"id": "volumes",  "label": "Restore Volumes",   "icon": "💾"},
    {"id": "start",    "label": "Start Stack",       "icon": "🚀"},
    {"id": "complete", "label": "Complete",           "icon": "✅"},
]


def _init_restore_progress(restore_id: str):
    with _restore_lock:
        _restore_progress[restore_id] = {
            "steps": {s["id"]: {"status": "pending", "detail": ""} for s in RESTORE_STEPS},
            "result": None,
            "error": None,
            "stack_name": "...",
        }


def _update_progress(restore_id: str, step: str, status: str, detail: str = ""):
    with _restore_lock:
        if restore_id in _restore_progress:
            _restore_progress[restore_id]["steps"][step] = {"status": status, "detail": detail}


def _run_restore_background(restore_id: str, job_id: str, file_path: Path, downloaded_temp: bool):
    """Background function that runs the restore and updates progress."""
    try:
        def progress_cb(step, status, detail):
            _update_progress(restore_id, step, status, detail)

        result = restore_engine.restore(file_path, progress_callback=progress_cb)
        with _restore_lock:
            if restore_id in _restore_progress:
                _restore_progress[restore_id]["result"] = result
                _restore_progress[restore_id]["stack_name"] = result.get("stack_name", "unknown")
    except Exception as e:
        logger.error(f"Restore background task failed: {e}", exc_info=True)
        with _restore_lock:
            if restore_id in _restore_progress:
                _restore_progress[restore_id]["error"] = str(e)
                _restore_progress[restore_id]["steps"]["complete"] = {"status": "error", "detail": str(e)}
    finally:
        if downloaded_temp and file_path.exists():
            try:
                file_path.unlink()
                logger.info(f"Cleaned up temp file: {file_path}")
            except Exception:
                pass


@router.post("/{job_id}/restore")
async def restore_backup(job_id: str, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    """Start a restore in the background and return a progress tracking div."""
    result = await db.execute(select(BackupJob).where(BackupJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job or not job.storage_path:
        return HTMLResponse(content="""
            <div class="toast toast-error"><span>❌</span> Backup record not found.</div>
        """)

    # Generate a unique restore ID
    restore_id = str(uuid.uuid4())[:8]
    _init_restore_progress(restore_id)

    file_path = Path(settings.LOCAL_BACKUP_DIR) / job.storage_path
    downloaded_temp = False

    if not file_path.exists():
        backend = settings.get_effective_storage_backend()
        if backend != "local":
            try:
                _update_progress(restore_id, "download", "running", f"Downloading from {backend}...")
                driver = settings.get_storage_driver()
                local_filename = f"{job.stack_name}_{job.created_at.strftime('%Y%m%d_%H%M%S')}.tar.gz"
                file_path = Path(settings.LOCAL_BACKUP_DIR) / local_filename
                logger.info(f"Downloading backup from {backend} ({job.storage_path}) to {file_path}")
                await driver.download(job.storage_path, file_path)
                downloaded_temp = True
                _update_progress(restore_id, "download", "done", f"Downloaded from {backend}")
            except Exception as e:
                logger.error(f"Failed to download backup from {backend}: {e}")
                _update_progress(restore_id, "download", "error", str(e)[:100])
                return HTMLResponse(content=f"""
                    <div class="toast toast-error">
                        <span>❌</span> Failed to download backup from {backend}: {str(e)[:200]}
                    </div>
                """)
        else:
            return HTMLResponse(content=f"""
                <div class="toast toast-error">
                    <span>❌</span> Backup file not found: <code>{job.storage_path}</code>
                </div>
            """)
    else:
        _update_progress(restore_id, "download", "done", "File available locally")

    # Store stack name
    with _restore_lock:
        if restore_id in _restore_progress:
            _restore_progress[restore_id]["stack_name"] = job.stack_name

    # Start restore in background thread
    import threading
    t = threading.Thread(
        target=_run_restore_background,
        args=(restore_id, job_id, file_path, downloaded_temp),
        daemon=True
    )
    t.start()

    # Return the progress tracker div that polls for updates
    return HTMLResponse(content=f"""
        <div id="restore-progress-{restore_id}"
             hx-get="/api/backups/restore/{restore_id}/progress"
             hx-trigger="load, every 1500ms"
             hx-swap="outerHTML">
            {_render_progress_html(restore_id)}
        </div>
    """)


@router.get("/restore/{restore_id}/progress")
async def get_restore_progress(restore_id: str):
    """Return current restore progress as styled HTML."""
    with _restore_lock:
        progress = _restore_progress.get(restore_id)

    if not progress:
        return HTMLResponse(content="""
            <div class="toast toast-error"><span>❌</span> Restore session not found.</div>
        """)

    result = progress.get("result")
    error = progress.get("error")

    # If complete (has result or error), render final state without polling
    if result or error:
        html = _render_progress_html(restore_id)
        # Add final result summary
        if result:
            status = result.get("status", "failed")
            stack = result.get("stack_name", "unknown")
            restored = result.get("volumes_restored", 0)
            found = result.get("volumes_found", 0)
            details = result.get("details", [])
            err = result.get("error")
            details_html = "".join(f"<li>{d}</li>" for d in details[-6:])  # Show last 6 details

            if status == "success":
                html += f"""
                <div class="toast toast-success" style="margin-top: 1rem;">
                    <div>
                        <strong>✅ Restore Complete — {stack}</strong><br>
                        <span style="font-size: 0.8rem;">{restored}/{found} volumes restored</span>
                        <ul style="margin-top: 0.5rem; font-size: 0.72rem; list-style: none; padding: 0; opacity: 0.85;">{details_html}</ul>
                    </div>
                </div>"""
            elif status in ("partial", "empty"):
                html += f"""
                <div class="toast toast-info" style="margin-top: 1rem;">
                    <div>
                        <strong>⚠️ Partial Restore — {stack}</strong><br>
                        <span style="font-size: 0.8rem;">{restored}/{found} volumes</span>
                        {f'<br><span style="font-size: 0.75rem; color: var(--warning);">{err}</span>' if err else ''}
                    </div>
                </div>"""
            else:
                html += f"""
                <div class="toast toast-error" style="margin-top: 1rem;">
                    <div>
                        <strong>❌ Restore Failed — {stack}</strong><br>
                        <span style="font-size: 0.8rem;">{err or 'Unknown error'}</span>
                    </div>
                </div>"""
        elif error:
            html += f"""
            <div class="toast toast-error" style="margin-top: 1rem;">
                <span>❌</span> Restore crashed: {error[:200]}
            </div>"""

        return HTMLResponse(content=f'<div id="restore-progress-{restore_id}">{html}</div>')

    # Still running — keep polling
    html = _render_progress_html(restore_id)
    return HTMLResponse(content=f"""
        <div id="restore-progress-{restore_id}"
             hx-get="/api/backups/restore/{restore_id}/progress"
             hx-trigger="every 1500ms"
             hx-swap="outerHTML">
            {html}
        </div>
    """)


def _render_progress_html(restore_id: str) -> str:
    """Render the progress steps card as HTML."""
    with _restore_lock:
        progress = _restore_progress.get(restore_id, {})

    steps = progress.get("steps", {})
    stack_name = progress.get("stack_name", "...")

    html = f"""
    <div class="card" style="padding: 1.5rem; margin-bottom: 1rem;">
        <div style="display: flex; align-items: center; gap: 0.75rem; margin-bottom: 1.25rem;">
            <div class="spinner" style="width: 1.2rem; height: 1.2rem;"></div>
            <strong style="font-family: 'Outfit', sans-serif; font-size: 1rem;">
                Restoring: {stack_name}
            </strong>
        </div>
        <div style="display: flex; flex-direction: column; gap: 0.5rem;">
    """

    for step_def in RESTORE_STEPS:
        sid = step_def["id"]
        step_state = steps.get(sid, {"status": "pending", "detail": ""})
        status = step_state["status"]
        detail = step_state["detail"]
        icon = step_def["icon"]
        label = step_def["label"]

        if status == "done":
            indicator = "✅"
            color = "var(--success)"
            opacity = "0.7"
        elif status == "running":
            indicator = '<div class="spinner" style="width: 0.9rem; height: 0.9rem; display: inline-block;"></div>'
            color = "var(--text)"
            opacity = "1"
        elif status == "error":
            indicator = "❌"
            color = "var(--error)"
            opacity = "1"
        else:
            indicator = '<span style="opacity: 0.3;">○</span>'
            color = "var(--text-muted)"
            opacity = "0.4"

        html += f"""
        <div style="display: flex; align-items: center; gap: 0.75rem; padding: 0.4rem 0; opacity: {opacity}; transition: opacity 0.3s;">
            <span style="width: 1.2rem; text-align: center; font-size: 0.85rem; flex-shrink: 0;">{indicator}</span>
            <span style="font-size: 0.85rem; color: {color}; font-weight: 500; min-width: 8rem;">{icon} {label}</span>
            <span style="font-size: 0.72rem; color: var(--text-muted); flex: 1;">{detail}</span>
        </div>"""

    html += """
        </div>
    </div>"""

    # Hide the spinner in header if all done
    result = progress.get("result")
    error = progress.get("error")
    if result or error:
        html = html.replace(
            '<div class="spinner" style="width: 1.2rem; height: 1.2rem;"></div>',
            '', 1
        )
        if result and result.get("status") == "success":
            html = html.replace("Restoring:", "Restored:")
        elif error or (result and result.get("status") == "failed"):
            html = html.replace("Restoring:", "Failed:")

    return html


@router.get("/{job_id}/inspect")
async def inspect_backup_endpoint(job_id: str, db: AsyncSession = Depends(get_db)):
    """Inspect what's inside a backup without restoring it."""
    result = await db.execute(select(BackupJob).where(BackupJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job or not job.storage_path:
        raise HTTPException(status_code=404, detail="Backup not found")
    
    file_path = Path(settings.LOCAL_BACKUP_DIR) / job.storage_path
    downloaded_temp = False
    if not file_path.exists():
        backend = settings.get_effective_storage_backend()
        if backend != "local":
            try:
                driver = settings.get_storage_driver()
                local_filename = f"{job.stack_name}_{job.created_at.strftime('%Y%m%d_%H%M%S')}.tar.gz"
                file_path = Path(settings.LOCAL_BACKUP_DIR) / local_filename
                await driver.download(job.storage_path, file_path)
                downloaded_temp = True
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to download from {backend}: {e}")
        else:
            raise HTTPException(status_code=404, detail="File not found on disk")
    
    from app.engine.restore import inspect_backup
    import asyncio
    loop = asyncio.get_event_loop()
    try:
        info = await loop.run_in_executor(None, inspect_backup, file_path)
        return info
    finally:
        if downloaded_temp and file_path.exists():
            try:
                file_path.unlink()
            except Exception:
                pass



