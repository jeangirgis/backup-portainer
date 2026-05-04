from fastapi import APIRouter, HTTPException
from pathlib import Path
from app.config import get_settings

router = APIRouter(tags=["Logs"])

@router.get("/logs")
async def get_logs(lines: int = 500):
    settings = get_settings()
    log_file_path = Path(settings.LOCAL_BACKUP_DIR) / "companion.log"
    
    if not log_file_path.exists():
        return {"logs": "Log file not found. Logs will appear here once the first event occurs."}
    
    try:
        # Read the last N lines efficiently
        with open(log_file_path, "r", encoding="utf-8") as f:
            # We'll just read all lines and take the last N since files shouldn't be bigger than 5MB
            # A more optimal way would be to seek from the end, but this is fine for a 5MB max file.
            all_lines = f.readlines()
            return {"logs": "".join(all_lines[-lines:])}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
