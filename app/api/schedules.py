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

@router.get("", response_model=List[ScheduleSchema])
async def list_schedules(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Schedule))
    schedules = result.scalars().all()
    
    if "hx-request" in request.headers:
        html = ""
        for s in schedules:
            status_color = "var(--success)" if s.enabled else "var(--text-muted)"
            html += f"""
            <tr id="schedule-{s.id}">
                <td><strong>{s.stack_name}</strong></td>
                <td><code>{s.cron_expression}</code></td>
                <td>{s.retention_days} days</td>
                <td><span style="color: {status_color}; font-weight: bold;">{'Enabled' if s.enabled else 'Disabled'}</span></td>
                <td>
                    <button class="btn btn-outline" style="color: var(--error); padding: 0.25rem 0.5rem;"
                            hx-delete="/api/schedules/{s.id}" hx-target="#schedule-{s.id}" hx-swap="outerHTML">
                        Delete
                    </button>
                </td>
            </tr>
            """
        return HTMLResponse(content=html)
    
    return schedules

@router.post("", response_model=ScheduleSchema)
async def create_schedule(data: ScheduleCreate, db: AsyncSession = Depends(get_db)):
    # Validate cron
    try:
        from apscheduler.triggers.cron import CronTrigger
        CronTrigger.from_crontab(data.cron_expression)
    except:
        raise HTTPException(status_code=400, detail="Invalid cron expression")

    sch = Schedule(**data.model_dump())
    db.add(sch)
    await db.commit()
    await db.refresh(sch)
    
    live_scheduler.add_job(sch)
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
