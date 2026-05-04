from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from app.db import init_db
from app.api.stacks import router as stacks_router
from app.api.backups import router as backups_router
from app.config import get_settings
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

settings = get_settings()

log_file_path = Path(settings.LOCAL_BACKUP_DIR) / "companion.log"
log_file_path.parent.mkdir(parents=True, exist_ok=True)

# Configure logging to show our debug messages
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(log_file_path, maxBytes=5*1024*1024, backupCount=3)
    ]
)
# Quiet down noisy libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.INFO)

# settings is already instantiated above

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()
    from app.scheduler import scheduler
    await scheduler.start()
    yield
    # Shutdown
    pass

app = FastAPI(title="Backtainer", lifespan=lifespan)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from fastapi.responses import JSONResponse

# Simple Bearer Token Middleware
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    # Skip auth for root and frontend assets
    if request.url.path == "/" or request.url.path.startswith("/frontend") or request.url.path.endswith(".html") or request.url.path.endswith(".css") or request.url.path.endswith(".js"):
        return await call_next(request)
    
    # Skip for favicon
    if request.url.path == "/favicon.ico":
        return await call_next(request)

    # Allow health check and debug
    if request.url.path in ["/health", "/api/health", "/api/debug/docker"]:
        return await call_next(request)

    # Check Authorization header or query parameter
    auth_header = request.headers.get("Authorization")
    token = None
    
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
    else:
        # Fallback to query parameter (useful for downloads)
        token = request.query_params.get("token")

    if not token:
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    
    try:
        if token != settings.SECRET_KEY:
            return JSONResponse(status_code=401, content={"detail": "Invalid token"})
    except Exception:
        return JSONResponse(status_code=401, content={"detail": "Invalid token format"})
    
    return await call_next(request)

from app.api.schedules import router as schedules_router
from app.api.settings import router as settings_router
from app.api.health import router as health_router
from app.api.logs import router as logs_router

# API Routers
app.include_router(stacks_router, prefix="/api")
app.include_router(backups_router, prefix="/api")
app.include_router(schedules_router, prefix="/api")
app.include_router(settings_router, prefix="/api")
app.include_router(health_router, prefix="/api")
app.include_router(logs_router, prefix="/api")


# Debug endpoint — shows ALL Docker containers and volumes (no auth required)
@app.get("/api/debug/docker")
async def debug_docker():
    import docker
    client = docker.from_env()
    
    containers = []
    for c in client.containers.list(all=True):
        labels = c.labels or {}
        mounts = []
        for m in c.attrs.get("Mounts", []):
            mounts.append({
                "type": m.get("Type"),
                "name": m.get("Name", m.get("Source", "?")),
                "dest": m.get("Destination"),
            })
        containers.append({
            "name": c.name,
            "status": c.status,
            "project": labels.get("com.docker.compose.project", "none"),
            "service": labels.get("com.docker.compose.service", "none"),
            "mounts": mounts,
        })
    
    volumes = []
    for v in client.volumes.list():
        labels = v.attrs.get("Labels", {}) or {}
        volumes.append({
            "name": v.name,
            "project": labels.get("com.docker.compose.project", "none"),
            "driver": v.attrs.get("Driver", "?"),
        })
    
    return {
        "containers": containers,
        "volumes": volumes,
        "total_containers": len(containers),
        "total_volumes": len(volumes),
    }


# Static Files
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")

