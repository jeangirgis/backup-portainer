from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime
from app.db import get_db
from app.models import Schedule
from app.scheduler import scheduler as live_scheduler

router = APIRouter(prefix="/schedules", tags=["schedules"])

# Human-readable cron descriptions
CRON_LABELS = {
    "0 2 * * *": "🌙 Daily at 2 AM",
    "0 2 * * 0": "📅 Weekly (Sun 2 AM)",
    "0 */6 * * *": "⚡ Every 6 hours",
    "0 */12 * * *": "🔄 Twice daily",
    "0 2 1 * *": "📆 Monthly (1st)",
    "0 3 * * *": "🌙 Daily at 3 AM",
    "0 0 * * *": "🌙 Daily at midnight",
    "0 */1 * * *": "⚡ Every hour",
}


def cron_to_human(cron: str) -> str:
    """Convert a cron expression to a human-readable label."""
    if cron in CRON_LABELS:
        return CRON_LABELS[cron]
    return f"🔧 {cron}"


class ScheduleSchema(BaseModel):
    id: str
    stack_id: str
    stack_name: str
    cron_expression: str
    retention_days: int
    enabled: bool
    last_run_at: Optional[datetime]
    created_at: datetime


class ScheduleCreate(BaseModel):
    stack_id: str
    stack_name: str
    cron_expression: str
    retention_days: int = 7


class ScheduleUpdate(BaseModel):
    stack_id: str
    stack_name: str
    cron_expression: str
    retention_days: int


@router.get("", response_model=List[ScheduleSchema])
async def list_schedules(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Schedule))
    schedules = result.scalars().all()
    
    if "hx-request" in request.headers:
        html = ""
        if not schedules:
            html = '<tr><td colspan="5" style="text-align: center; padding: 2rem; color: var(--text-muted);">No schedules yet. Create one on the right →</td></tr>'
            return HTMLResponse(content=html)

        for s in schedules:
            freq_label = cron_to_human(s.cron_expression)
            retention_label = f"{s.retention_days} days" if s.retention_days > 0 else "Forever"
            status_class = "status-running" if s.enabled else "status-stopped"
            status_text = "Active" if s.enabled else "Paused"
            last_run = s.last_run_at.strftime("%Y-%m-%d %H:%M") if s.last_run_at else "Never"

            html += f"""
            <tr id="schedule-{s.id}">
                <td>
                    <strong>{s.stack_name}</strong>
                    <div style="font-size: 0.7rem; color: var(--text-muted); margin-top: 0.15rem;">Last run: {last_run}</div>
                </td>
                <td>{freq_label}</td>
                <td>{retention_label}</td>
                <td><span class="status-badge {status_class}">{status_text}</span></td>
                <td>
                    <button class="btn btn-sm btn-outline"
                            onclick="editSchedule('{s.id}', '{s.stack_id}', '{s.stack_name}', '{s.cron_expression}', {s.retention_days})">
                        Edit
                    </button>
                    <button class="btn btn-sm btn-danger"
                            hx-delete="/api/schedules/{s.id}" hx-target="#schedule-{s.id}" hx-swap="outerHTML"
                            hx-confirm="Delete this schedule?">
                        Delete
                    </button>
                </td>
            </tr>
            """
        return HTMLResponse(content=html)
    
    return schedules


@router.post("", response_model=ScheduleSchema)
async def create_schedule(data: ScheduleCreate, request: Request, db: AsyncSession = Depends(get_db)):
    # Validate cron
    try:
        from apscheduler.triggers.cron import CronTrigger
        CronTrigger.from_crontab(data.cron_expression)
    except Exception:
        if "hx-request" in request.headers:
            return HTMLResponse(
                content=f'<tr><td colspan="5"><div class="toast toast-error">❌ Invalid cron expression: {data.cron_expression}</div></td></tr>',
                status_code=200,
            )
        raise HTTPException(status_code=400, detail="Invalid cron expression")

    sch = Schedule(**data.model_dump())
    db.add(sch)
    await db.commit()
    await db.refresh(sch)
    
    live_scheduler.add_job(sch)

    # Return HTML row for HTMX
    if "hx-request" in request.headers:
        freq_label = cron_to_human(sch.cron_expression)
        retention_label = f"{sch.retention_days} days" if sch.retention_days > 0 else "Forever"
        return HTMLResponse(content=f"""
            <tr id="schedule-{sch.id}">
                <td>
                    <strong>{sch.stack_name}</strong>
                    <div style="font-size: 0.7rem; color: var(--text-muted); margin-top: 0.15rem;">Last run: Never</div>
                </td>
                <td>{freq_label}</td>
                <td>{retention_label}</td>
                <td><span class="status-badge status-running">Active</span></td>
                <td>
                    <button class="btn btn-sm btn-outline"
                            onclick="editSchedule('{sch.id}', '{sch.stack_id}', '{sch.stack_name}', '{sch.cron_expression}', {sch.retention_days})">
                        Edit
                    </button>
                    <button class="btn btn-sm btn-danger"
                            hx-delete="/api/schedules/{sch.id}" hx-target="#schedule-{sch.id}" hx-swap="outerHTML"
                            hx-confirm="Delete this schedule?">
                        Delete
                    </button>
                </td>
            </tr>
        """)

    return sch


@router.delete("/{schedule_id}")
async def delete_schedule(schedule_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    sch = result.scalar_one_or_none()
    if not sch:
        raise HTTPException(status_code=404, detail="Schedule not found")
    
    live_scheduler.remove_job(sch.id)
    await db.delete(sch)
    await db.commit()
    return HTMLResponse(content="")

@router.put("/{schedule_id}", response_model=ScheduleSchema)
async def update_schedule(schedule_id: str, data: ScheduleUpdate, request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    sch = result.scalar_one_or_none()
    if not sch:
        if "hx-request" in request.headers:
            return HTMLResponse(
                content=f'<tr><td colspan="5"><div class="toast toast-error">❌ Schedule not found</div></td></tr>',
                status_code=404,
            )
        raise HTTPException(status_code=404, detail="Schedule not found")

    # Validate cron
    try:
        from apscheduler.triggers.cron import CronTrigger
        CronTrigger.from_crontab(data.cron_expression)
    except Exception:
        if "hx-request" in request.headers:
            return HTMLResponse(
                content=f'<tr><td colspan="5"><div class="toast toast-error">❌ Invalid cron expression: {data.cron_expression}</div></td></tr>',
                status_code=200,
            )
        raise HTTPException(status_code=400, detail="Invalid cron expression")

    sch.stack_id = data.stack_id
    sch.stack_name = data.stack_name
    sch.cron_expression = data.cron_expression
    sch.retention_days = data.retention_days
    
    await db.commit()
    await db.refresh(sch)
    
    # Update live scheduler job
    if sch.enabled:
        live_scheduler.add_job(sch)

    # Return HTML row for HTMX
    if "hx-request" in request.headers:
        freq_label = cron_to_human(sch.cron_expression)
        retention_label = f"{sch.retention_days} days" if sch.retention_days > 0 else "Forever"
        status_class = "status-running" if sch.enabled else "status-stopped"
        status_text = "Active" if sch.enabled else "Paused"
        last_run = sch.last_run_at.strftime("%Y-%m-%d %H:%M") if sch.last_run_at else "Never"
        return HTMLResponse(content=f"""
            <tr id="schedule-{sch.id}">
                <td>
                    <strong>{sch.stack_name}</strong>
                    <div style="font-size: 0.7rem; color: var(--text-muted); margin-top: 0.15rem;">Last run: {last_run}</div>
                </td>
                <td>{freq_label}</td>
                <td>{retention_label}</td>
                <td><span class="status-badge {status_class}">{status_text}</span></td>
                <td>
                    <button class="btn btn-sm btn-outline"
                            onclick="editSchedule('{sch.id}', '{sch.stack_id}', '{sch.stack_name}', '{sch.cron_expression}', {sch.retention_days})">
                        Edit
                    </button>
                    <button class="btn btn-sm btn-danger"
                            hx-delete="/api/schedules/{sch.id}" hx-target="#schedule-{sch.id}" hx-swap="outerHTML"
                            hx-confirm="Delete this schedule?">
                        Delete
                    </button>
                </td>
            </tr>
        """)

    return sch

