# Portainer Backup Companion — Full Implementation Plan

> **Version:** 1.0 | **Stack:** Python FastAPI + HTMX + Docker SDK | **Deploy:** Portainer Stack

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture](#2-architecture)
3. [Project File Structure](#3-project-file-structure)
4. [Configuration — Environment Variables](#4-configuration--environment-variables)
5. [REST API Specification](#5-rest-api-specification)
6. [Data Models](#6-data-models)
7. [Detailed Implementation Instructions](#7-detailed-implementation-instructions)
8. [Docker Configuration](#8-docker-configuration)
9. [Implementation Phases](#9-implementation-phases)
10. [Error Handling Requirements](#10-error-handling-requirements)
11. [Testing Requirements](#11-testing-requirements)

---

## 1. Project Overview

The Portainer Backup Companion is a self-hosted Docker container that runs alongside Portainer and provides on-demand and scheduled backup functionality for Docker stacks, volumes, and container configurations. It exposes a simple browser-based dashboard and a REST API, and can be linked from Portainer's custom links or bookmarks feature.

### 1.1 Goals

- Back up any Docker stack: compose file, environment variables, container labels
- Back up Docker volumes as compressed tar archives
- Bundle everything into a single timestamped `.tar.gz` with a manifest
- Support on-demand (manual) and scheduled (cron) backups
- Support multiple storage backends: local disk, S3-compatible, SFTP
- Send notifications on backup success or failure
- Allow restore from a previously created backup bundle
- Require no modifications to Portainer itself

### 1.2 Non-Goals

- This is **NOT** a Portainer plugin or extension — it is a companion container
- It does **NOT** back up the Portainer database itself (out of scope for v1)
- It does **NOT** support Windows containers
- It does **NOT** require Portainer Business Edition

### 1.3 Tech Stack Summary

| Component | Technology & Reason |
|---|---|
| Backend | Python 3.11 + FastAPI — async, fast, clean REST, easy Dockerization |
| Scheduler | APScheduler 3.x — runs inside the FastAPI process, no extra container |
| Frontend | HTMX + plain HTML/CSS — no build step, lightweight, works in all browsers |
| Docker integration | docker Python SDK (docker-py) via Unix socket mount |
| Portainer integration | Portainer HTTP API with JWT token auth |
| S3 storage | boto3 — supports AWS S3, MinIO, Cloudflare R2 |
| Config | Environment variables + optional `config.yml` file |
| Packaging | Docker + `docker-compose.yml` — deploy as Portainer stack |

---

## 2. Architecture

### 2.1 High-Level Design

The companion runs as a single Docker container with the Docker socket mounted read-write (needed for volume inspection and tarring). It communicates with Portainer via its HTTP API to retrieve stack definitions, and with the Docker daemon directly via the socket for volume data. All backup operations are performed by the backup engine inside the container.

```
┌─────────────────────────────────────────────────────────┐
│  User layer                                             │
│                                                         │
│  [ Portainer UI ] ──bookmark──► [ Backup Companion UI ] │
└─────────────────────────────────────────────────────────┘
                                         │
                                         ▼
┌─────────────────────────────────────────────────────────┐
│  Application layer                                      │
│                                                         │
│         [ FastAPI Backend + APScheduler ]               │
└────────────┬────────────────┬───────────────────────────┘
             │                │
             ▼                ▼
┌────────────────────┐  ┌─────────────────────────────────┐
│  Portainer API     │  │  Docker Socket                  │
│  Stack configs     │  │  Volumes, container state        │
└────────────────────┘  └─────────────────────────────────┘
             │                │
             ▼                ▼
┌─────────────────────────────────────────────────────────┐
│  Backup Engine                                          │
│                                                         │
│  [ Stack Exporter ] [ Volume Exporter ] [ Packager ]    │
└─────────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────┐
│  Storage layer                                          │
│                                                         │
│  [ Local Disk ]   [ S3 / MinIO / R2 ]   [ SFTP / NFS ] │
└─────────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────┐
│  Notifications                                          │
│                                                         │
│  [ Slack Webhook ]  [ Email SMTP ]  [ Generic Webhook ] │
└─────────────────────────────────────────────────────────┘
```

### 2.2 Component Breakdown

| Component | Responsibility |
|---|---|
| `main.py` | FastAPI app — HTTP endpoints, auth middleware, CORS |
| `engine.py` | Orchestrates stack export, volume tar, packaging, storage upload |
| `stack_exporter.py` | Calls Portainer API to get compose YAML + env vars + labels |
| `volume_exporter.py` | Uses docker-py to stream volume data out as `.tar` |
| `packager.py` | Combines exports into timestamped `.tar.gz` + `manifest.json` |
| `storage/` | `local.py`, `s3.py`, `sftp.py` — pluggable storage backends |
| `scheduler.py` | APScheduler — loads cron jobs from DB/config, fires backup engine |
| `notifier.py` | Sends Slack, email, or webhook notifications on events |
| `frontend/` | HTMX HTML pages served by FastAPI's StaticFiles |
| `db.py` | SQLite via SQLAlchemy — stores job history, schedules, settings |

### 2.3 Data Flow — Manual Backup

| Step | Description |
|---|---|
| 1 | User clicks **Backup** in the dashboard |
| 2 | Frontend `POST /api/backup/{stack_id}` |
| 3 | `BackupEngine.run()` spawns an async task |
| 4 | `StackExporter` calls Portainer API — saves `docker-compose.yml` + `.env` |
| 5 | `VolumeExporter` iterates attached volumes — `docker cp` stream → `.tar` per volume |
| 6 | `Packager` assembles: `manifest.json` + compose + envs + volume tars → `.tar.gz` |
| 7 | `StorageDriver` writes bundle to configured backend(s) |
| 8 | Job record saved to SQLite (status, size, path, timestamp) |
| 9 | `Notifier` sends success/failure alert if configured |
| 10 | Frontend polls `GET /api/backup/{job_id}/status` — shows result |

---

## 3. Project File Structure

The AI must produce the following file tree exactly. Every file listed here must be created.

```
portainer-backup-companion/
├── docker-compose.yml             # Deploy as Portainer stack
├── Dockerfile                     # Multi-stage build
├── .env.example                   # All env vars documented
├── README.md                      # Setup + usage guide
├── requirements.txt               # Python deps
│
├── app/
│   ├── main.py                    # FastAPI app entrypoint
│   ├── config.py                  # Pydantic settings from env
│   ├── db.py                      # SQLAlchemy + SQLite setup
│   ├── models.py                  # ORM models: BackupJob, Schedule
│   ├── scheduler.py               # APScheduler init + job management
│   ├── notifier.py                # Slack / email / webhook notifications
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   ├── backups.py             # /api/backup endpoints
│   │   ├── stacks.py              # /api/stacks endpoints
│   │   ├── schedules.py           # /api/schedules endpoints
│   │   └── settings.py            # /api/settings endpoints
│   │
│   ├── engine/
│   │   ├── __init__.py
│   │   ├── engine.py              # BackupEngine orchestrator
│   │   ├── stack_exporter.py      # Portainer API → compose YAML
│   │   ├── volume_exporter.py     # Docker socket → volume tar
│   │   ├── packager.py            # Bundle into .tar.gz + manifest
│   │   └── restore.py             # Unpack + restore a backup bundle
│   │
│   └── storage/
│       ├── __init__.py
│       ├── base.py                # Abstract StorageDriver base class
│       ├── local.py               # Local disk driver
│       ├── s3.py                  # S3/MinIO/R2 driver (boto3)
│       └── sftp.py                # SFTP driver (paramiko)
│
└── frontend/
    ├── index.html                 # Dashboard — stack list + status
    ├── backups.html               # Backup history + download
    ├── schedules.html             # Cron schedule management
    ├── settings.html              # Connection settings form
    ├── style.css                  # Minimal clean stylesheet
    └── app.js                     # HTMX helpers + status polling
```

---

## 4. Configuration — Environment Variables

All configuration is done via environment variables. The `.env.example` file must document every variable. The app reads them at startup via Pydantic `BaseSettings`.

### 4.1 Required Variables

| Variable | Default | Description |
|---|---|---|
| `PORTAINER_URL` | *(required)* | Base URL of Portainer, e.g. `http://portainer:9000` |
| `PORTAINER_API_TOKEN` | *(required)* | Portainer API token (generate in Portainer settings) |
| `SECRET_KEY` | *(required)* | Random string for signing internal sessions |
| `STORAGE_BACKEND` | `local` | One of: `local`, `s3`, `sftp` |
| `LOCAL_BACKUP_DIR` | `/backups` | Path inside container for local storage |

### 4.2 S3 Storage Variables (`STORAGE_BACKEND=s3`)

| Variable | Default | Description |
|---|---|---|
| `S3_BUCKET` | *(required)* | S3 bucket name |
| `S3_ACCESS_KEY` | *(required)* | AWS/MinIO access key ID |
| `S3_SECRET_KEY` | *(required)* | AWS/MinIO secret access key |
| `S3_ENDPOINT_URL` | `None` (AWS) | Custom endpoint for MinIO or R2 |
| `S3_REGION` | `us-east-1` | AWS region |
| `S3_PREFIX` | `backups/` | Key prefix for all backup objects |

### 4.3 SFTP Storage Variables (`STORAGE_BACKEND=sftp`)

| Variable | Default | Description |
|---|---|---|
| `SFTP_HOST` | *(required)* | SFTP server hostname |
| `SFTP_PORT` | `22` | SFTP port |
| `SFTP_USER` | *(required)* | SFTP username |
| `SFTP_PASSWORD` | `None` | SFTP password (use key auth if omitted) |
| `SFTP_KEY_PATH` | `None` | Path to private key file inside container |
| `SFTP_REMOTE_DIR` | `/backups` | Remote directory for backup files |

### 4.4 Notification Variables (all optional)

| Variable | Default | Description |
|---|---|---|
| `NOTIFY_SLACK_WEBHOOK` | `None` | Slack incoming webhook URL |
| `NOTIFY_EMAIL_TO` | `None` | Recipient email address |
| `NOTIFY_EMAIL_FROM` | `None` | Sender email address |
| `SMTP_HOST` | `None` | SMTP server hostname |
| `SMTP_PORT` | `587` | SMTP port (587 for TLS) |
| `SMTP_USER` | `None` | SMTP username |
| `SMTP_PASSWORD` | `None` | SMTP password |
| `NOTIFY_WEBHOOK_URL` | `None` | Generic HTTP POST webhook on events |

---

## 5. REST API Specification

All endpoints are prefixed with `/api`. All responses are JSON unless noted. Authentication uses a `Bearer` token passed as `Authorization` header (the `SECRET_KEY` value). The frontend uses this automatically.

### 5.1 Stacks Endpoints

| Method | Path | Response | Description |
|---|---|---|---|
| `GET` | `/api/stacks` | `List[StackInfo]` | List all stacks from Portainer with volume counts |
| `GET` | `/api/stacks/{id}` | `StackDetail` | Single stack detail: compose, volumes, last backup |

### 5.2 Backup Endpoints

| Method | Path | Response | Description |
|---|---|---|---|
| `POST` | `/api/backup/{stack_id}` | `BackupJob` | Start a backup job for the given stack |
| `GET` | `/api/backup/{job_id}/status` | `BackupJob` | Poll job status: `pending`, `running`, `success`, `failed` |
| `GET` | `/api/backups` | `List[BackupJob]` | All historical backup jobs (paged) |
| `GET` | `/api/backups/{job_id}/download` | File stream | Download the `.tar.gz` bundle |
| `DELETE` | `/api/backups/{job_id}` | `204` | Delete backup record and file |
| `POST` | `/api/restore` | `RestoreJob` | Upload + restore a `.tar.gz` bundle |

### 5.3 Schedule Endpoints

| Method | Path | Response | Description |
|---|---|---|---|
| `GET` | `/api/schedules` | `List[Schedule]` | All configured cron schedules |
| `POST` | `/api/schedules` | `Schedule` | Create schedule: `{stack_id, cron, retention_days}` |
| `PUT` | `/api/schedules/{id}` | `Schedule` | Update schedule (cron string or retention) |
| `DELETE` | `/api/schedules/{id}` | `204` | Remove a schedule |

### 5.4 Settings Endpoints

| Method | Path | Response | Description |
|---|---|---|---|
| `GET` | `/api/settings` | `Settings` | Current config (no secrets exposed) |
| `POST` | `/api/settings/test` | `TestResult` | Test Portainer API connectivity |

### 5.5 Pydantic Response Schemas

```python
# StackInfo — returned by GET /api/stacks
class StackInfo(BaseModel):
    id: str
    name: str
    status: str          # "running" | "stopped"
    volume_count: int
    last_backup_at: Optional[datetime]

# BackupJob — returned by all backup endpoints
class BackupJob(BaseModel):
    id: str              # UUID
    stack_id: str
    stack_name: str
    status: str          # "pending" | "running" | "success" | "failed"
    storage_path: Optional[str]
    size_bytes: Optional[int]
    error_message: Optional[str]
    created_at: datetime
    completed_at: Optional[datetime]
    triggered_by: str    # "manual" | "schedule:{id}"

# Schedule
class Schedule(BaseModel):
    id: str              # UUID
    stack_id: str
    stack_name: str
    cron_expression: str # e.g. "0 2 * * *"
    retention_days: int  # 0 = keep all
    enabled: bool
    last_run_at: Optional[datetime]
    created_at: datetime
```

---

## 6. Data Models

### 6.1 SQLite Tables (via SQLAlchemy)

#### `backup_jobs`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | Auto-generated UUID |
| `stack_id` | String | Portainer stack ID |
| `stack_name` | String | Snapshot of stack name at backup time |
| `status` | Enum | `pending` \| `running` \| `success` \| `failed` |
| `storage_path` | String | Relative path or S3 key of the bundle |
| `size_bytes` | Integer | Size of `.tar.gz` bundle |
| `error_message` | Text | Null on success, error string on failure |
| `created_at` | DateTime | UTC timestamp of job creation |
| `completed_at` | DateTime | UTC timestamp of completion |
| `triggered_by` | String | `manual` \| `schedule:{id}` |

#### `schedules`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | Auto-generated UUID |
| `stack_id` | String | Portainer stack ID to back up |
| `stack_name` | String | Cached name for display |
| `cron_expression` | String | Standard 5-part cron: `0 2 * * *` |
| `retention_days` | Integer | Delete backups older than N days (0 = keep all) |
| `enabled` | Boolean | `False` = paused but not deleted |
| `last_run_at` | DateTime | UTC of last successful run |
| `created_at` | DateTime | UTC of schedule creation |

### 6.2 Backup Bundle Format

Every backup is a `.tar.gz` with the following internal layout:

```
backup-{stack_name}-{YYYYMMDD-HHmmss}.tar.gz
├── manifest.json           # Metadata: stack, volumes, timestamps, app version
├── stack/
│   ├── docker-compose.yml  # Full compose file from Portainer API
│   └── stack.env           # Environment variables (one KEY=VALUE per line)
└── volumes/
    ├── {volume_name_1}.tar # Raw volume data (docker-py get_archive)
    └── {volume_name_2}.tar
```

#### `manifest.json` Schema

```json
{
  "version": "1.0",
  "app_version": "1.0.0",
  "created_at": "2024-01-15T02:00:00Z",
  "stack": {
    "id": "7",
    "name": "my-app",
    "portainer_url": "http://portainer:9000"
  },
  "volumes": [
    { "name": "my-app_data", "driver": "local", "size_bytes": 1048576 }
  ],
  "storage_backend": "s3",
  "checksums": {
    "docker-compose.yml": "sha256:abc123...",
    "volumes/my-app_data.tar": "sha256:def456..."
  }
}
```

---

## 7. Detailed Implementation Instructions

The following are per-file instructions the AI must follow exactly. Incomplete implementation is a defect.

### 7.1 `app/config.py`

- Use `pydantic-settings` `BaseSettings`. All env vars from Section 4 must map to typed fields.
- S3, SFTP, and notification fields must be `Optional` with `None` defaults.
- Provide a `get_storage_driver()` factory method that returns the correct `StorageDriver` subclass based on `STORAGE_BACKEND`.
- Provide a `get_settings()` function with `@lru_cache` so settings are only loaded once.

### 7.2 `app/db.py` and `app/models.py`

- Use SQLAlchemy 2.x with async support (`AsyncSession`, `create_async_engine`).
- Database file path: `{LOCAL_BACKUP_DIR}/companion.db`
- `models.py` must define `BackupJob` and `Schedule` as SQLAlchemy ORM models matching the schema in Section 6.1.
- Run `Base.metadata.create_all()` on startup via a FastAPI lifespan event.

### 7.3 `app/main.py`

- Use FastAPI with a `lifespan` context manager (not deprecated `on_event`).
- In the lifespan startup: initialize the DB, start APScheduler, load schedules from DB.
- Mount `frontend/` as `StaticFiles` at `/` with `html=True`.
- Include all API routers from `app/api/`.
- Add CORS middleware allowing all origins (self-hosted, no auth concern).
- Add a simple Bearer token middleware: compare `Authorization: Bearer {token}` to `SECRET_KEY`. Return `401` if missing or wrong. Skip auth for `GET /` and static files.

### 7.4 `app/engine/stack_exporter.py`

The `StackExporter` class must:

- Use `httpx.AsyncClient` to call the Portainer API.
- Authenticate with `X-API-Key: {PORTAINER_API_TOKEN}` header.
- `GET /api/stacks` to list all stacks.
- `GET /api/stacks/{id}` to get a single stack's metadata.
- `GET /api/stacks/{id}/file` to get the raw `docker-compose.yml` text.
- Save compose file as `stack/docker-compose.yml` in the provided temp directory.
- Save env vars as `stack/stack.env` (one `KEY=VALUE` per line).
- Raise `PortainerAuthError` on 401, `PortainerStackNotFoundError` on 404, `PortainerConnectionError` on connection failure.

### 7.5 `app/engine/volume_exporter.py`

The `VolumeExporter` class must:

- Use the docker Python SDK: `docker.from_env()`.
- Accept a list of volume names and a temp output directory.
- For each volume:
  1. Create a temporary `alpine` container with the volume mounted at `/data`.
  2. Use `container.get_archive('/data')` to stream the tar data out.
  3. Write the stream to `volumes/{volume_name}.tar` in the temp directory.
  4. Remove the temporary container in a `try/finally` block using `container.remove(force=True)`.
- **IMPORTANT:** Use `auto_remove=False` when creating the temp container. Using `auto_remove=True` causes a race condition with `get_archive`.
- Log the size of each exported volume tar.
- If a volume is not found, log a warning and skip it — do not abort the entire backup.

### 7.6 `app/engine/packager.py`

The `Packager` class must:

- Accept a temp directory containing `stack/` and `volumes/` subdirs.
- Compute `sha256` checksums for every file in the bundle.
- Generate `manifest.json` (see schema in Section 6.2).
- Write everything into a `.tar.gz` using Python's `tarfile` module with `mode='w:gz'`.
- Name the bundle: `backup-{stack_name}-{YYYYMMDD-HHmmss}.tar.gz`.
- Return the absolute path and size (bytes) of the resulting bundle.

### 7.7 `app/engine/engine.py`

The `BackupEngine` orchestrator must:

- Expose a single async method: `async def run(stack_id: str, triggered_by: str) -> BackupJob`.
- Create a `BackupJob` record in SQLite with `status="pending"` immediately.
- Use `tempfile.mkdtemp()` for a working directory; clean it up in `finally`.
- Call `StackExporter`, `VolumeExporter`, `Packager`, `StorageDriver.upload()` in sequence.
- Update job `status` to `"running"` before work begins.
- On any exception: set `status="failed"`, set `error_message`, call `Notifier.on_failure()`.
- On success: set `status="success"`, set `storage_path`, `size_bytes`, `completed_at`, call `Notifier.on_success()`.
- All status updates must be committed to SQLite immediately (not batched).

### 7.8 `app/storage/base.py`

Define an abstract base class `StorageDriver` with these abstract async methods:

```python
class StorageDriver(ABC):
    @abstractmethod
    async def upload(self, local_path: Path, remote_name: str) -> str:
        """Upload file. Returns storage path/key."""

    @abstractmethod
    async def download(self, remote_path: str, local_path: Path) -> None:
        """Download file to local_path."""

    @abstractmethod
    async def delete(self, remote_path: str) -> None:
        """Delete a backup file."""

    @abstractmethod
    async def list_backups(self) -> List[dict]:
        """Return list of {name, size, modified_at} dicts."""
```

### 7.9 `app/storage/s3.py`

The `S3Driver` must:

- Use `boto3` with `asyncio` via `run_in_executor` (boto3 is not async-native).
- Support custom `endpoint_url` for MinIO and Cloudflare R2.
- Store files at: `{S3_PREFIX}{remote_name}`.
- Use multipart upload for files larger than 100 MB.
- Return the full S3 URI (`s3://{bucket}/{key}`) as the storage path.
- Raise `S3AuthError` on credential failures, `S3BucketError` if bucket not found.

### 7.10 `app/storage/sftp.py`

The `SFTPDriver` must:

- Use `paramiko` for SFTP operations wrapped in `run_in_executor`.
- Support both password and private key authentication.
- Create `SFTP_REMOTE_DIR` if it does not exist.
- Raise `SFTPConnectionError` on connection failures.

### 7.11 `app/scheduler.py`

The scheduler module must:

- Initialize an APScheduler `AsyncIOScheduler` at app startup.
- On startup: load all `enabled=True` schedules from the database and register them as `CronTrigger` jobs.
- Expose functions: `add_schedule(schedule)`, `remove_schedule(schedule_id)`, `update_schedule(schedule)`.
- When a schedule is created/updated/deleted via API: call the corresponding scheduler function to update the live scheduler immediately (no restart needed).
- Each job calls `BackupEngine.run(stack_id, triggered_by=f'schedule:{schedule_id}')`.
- After each successful run: query for backup jobs for this stack older than `retention_days` and delete their files and records.

### 7.12 `app/notifier.py`

The `Notifier` class must:

- Expose two async methods: `on_success(job: BackupJob)` and `on_failure(job: BackupJob)`.
- Check which notification channels are configured (non-None) and send to all of them.
- Slack: POST to `NOTIFY_SLACK_WEBHOOK` with a JSON body containing `{"text": "..."}`.
- Email: use `aiosmtplib` or `smtplib` in executor with the SMTP settings.
- Webhook: POST to `NOTIFY_WEBHOOK_URL` with the full `BackupJob` JSON as body.
- Never raise exceptions — catch all errors and log them (notification failure must not affect backup status).

### 7.13 `frontend/index.html` — Dashboard

The dashboard must:

- Load HTMX from CDN: `https://unpkg.com/htmx.org@1.9.10`
- Show all stacks in a card grid: name, status (running/stopped), volume count, last backup date.
- Each card has a **Backup Now** button that `POST`s to `/api/backup/{stack_id}` via HTMX.
- After clicking Backup Now: poll `GET /api/backup/{job_id}/status` every 2 seconds using `hx-trigger="every 2s"`. Stop polling when status is `success` or `failed`.
- Show a spinner/progress bar while the job is running.
- Show success (green) or failure (red with error message) when done.
- Navigation links to `backups.html`, `schedules.html`, `settings.html`.
- The `Authorization: Bearer {SECRET_KEY}` header must be added to all HTMX requests via `htmx:configRequest` event handler in `app.js`.

### 7.14 `frontend/backups.html` — Backup History

Must show:

- Table of all past backup jobs: stack name, date, size, status, storage path.
- A **Download** button for each successful backup.
- A **Delete** button for each backup (with confirmation prompt).
- Pagination if more than 20 records.

### 7.15 `frontend/schedules.html` — Schedule Management

Must show:

- List of all configured cron schedules with stack name, cron expression, retention days, enabled toggle, last run time.
- A form to create a new schedule: select stack from dropdown, enter cron expression, enter retention days.
- Edit and Delete buttons per schedule.
- A cron expression helper text showing next 3 run times (compute in JS using a simple cron parser).

### 7.16 `frontend/settings.html` — Settings

Must show:

- Read-only display of current configuration (Portainer URL, storage backend, notification channels).
- A **Test Connection** button that calls `POST /api/settings/test` and shows the result.
- Instructions for generating a Portainer API token.

---

## 8. Docker Configuration

### 8.1 `Dockerfile`

Use a multi-stage build to keep the final image small:

```dockerfile
# Stage 1 — builder
FROM python:3.11-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Stage 2 — runtime
FROM python:3.11-slim
WORKDIR /app
COPY --from=builder /root/.local /root/.local
COPY app/ ./app/
COPY frontend/ ./frontend/
ENV PATH=/root/.local/bin:$PATH
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 8.2 `docker-compose.yml`

This is the file users deploy as a Portainer stack:

```yaml
version: '3.8'

services:
  backup-companion:
    image: portainer-backup-companion:latest
    build: .
    container_name: portainer-backup-companion
    restart: unless-stopped
    ports:
      - "8765:8000"   # Change left port if 8765 is taken
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock  # Required
      - backup_data:/backups                        # Local backup storage
    environment:
      - PORTAINER_URL=${PORTAINER_URL}
      - PORTAINER_API_TOKEN=${PORTAINER_API_TOKEN}
      - SECRET_KEY=${SECRET_KEY}
      - STORAGE_BACKEND=${STORAGE_BACKEND:-local}
      - LOCAL_BACKUP_DIR=/backups
    env_file:
      - .env

volumes:
  backup_data:
```

### 8.3 `requirements.txt`

```
fastapi==0.110.0
uvicorn[standard]==0.29.0
httpx==0.27.0
docker==7.0.0
boto3==1.34.0
paramiko==3.4.0
apscheduler==3.10.4
sqlalchemy==2.0.29
aiosqlite==0.20.0
pydantic-settings==2.2.1
python-multipart==0.0.9
aiofiles==23.2.1
```

### 8.4 `.env.example`

```bash
# ── Required ──────────────────────────────────────────
PORTAINER_URL=http://portainer:9000
PORTAINER_API_TOKEN=your_portainer_api_token_here
SECRET_KEY=change_me_to_a_random_string

# ── Storage ───────────────────────────────────────────
STORAGE_BACKEND=local          # local | s3 | sftp
LOCAL_BACKUP_DIR=/backups

# ── S3 (when STORAGE_BACKEND=s3) ──────────────────────
# S3_BUCKET=my-bucket
# S3_ACCESS_KEY=access_key
# S3_SECRET_KEY=secret_key
# S3_ENDPOINT_URL=http://minio:9000   # omit for AWS
# S3_REGION=us-east-1
# S3_PREFIX=backups/

# ── SFTP (when STORAGE_BACKEND=sftp) ──────────────────
# SFTP_HOST=backup-server.example.com
# SFTP_PORT=22
# SFTP_USER=backup
# SFTP_PASSWORD=
# SFTP_KEY_PATH=/run/secrets/sftp_key
# SFTP_REMOTE_DIR=/backups

# ── Notifications (all optional) ──────────────────────
# NOTIFY_SLACK_WEBHOOK=https://hooks.slack.com/services/...
# NOTIFY_EMAIL_TO=admin@example.com
# NOTIFY_EMAIL_FROM=backup@example.com
# SMTP_HOST=smtp.example.com
# SMTP_PORT=587
# SMTP_USER=user
# SMTP_PASSWORD=password
# NOTIFY_WEBHOOK_URL=https://your-webhook.example.com/hook
```

---

## 9. Implementation Phases

Implement in this order. Each phase must be fully working before starting the next.

### Phase 1 — Core (Must Have)

| # | Task | Files | Done when |
|---|---|---|---|
| 1 | Project scaffold | All dirs + empty files | File tree matches Section 3 |
| 2 | Config + DB | `config.py`, `db.py`, `models.py` | App starts without errors |
| 3 | Stack exporter | `stack_exporter.py` | Returns compose YAML from Portainer |
| 4 | Volume exporter | `volume_exporter.py` | Produces `.tar` file per volume |
| 5 | Packager | `packager.py` | Produces valid `.tar.gz` + manifest |
| 6 | Local storage driver | `storage/local.py` | Files saved to `/backups` |
| 7 | Backup engine | `engine.py`, `api/backups.py` | `POST /api/backup/{id}` works end-to-end |
| 8 | Stacks API | `api/stacks.py` | `GET /api/stacks` returns stack list |
| 9 | Dashboard UI | `index.html`, `backups.html` | Manual backup works in browser |
| 10 | Docker files | `Dockerfile`, `docker-compose.yml` | `docker compose up` builds and runs |

### Phase 2 — Scheduling & Notifications

| # | Task | Files | Done when |
|---|---|---|---|
| 11 | APScheduler setup | `scheduler.py` | Jobs persist across restarts |
| 12 | Schedule API + UI | `api/schedules.py`, `schedules.html` | Cron schedules fire correctly |
| 13 | Retention enforcement | `scheduler.py` | Old backups deleted per schedule |
| 14 | Slack notifications | `notifier.py` | Slack webhook fires on success/fail |
| 15 | Email notifications | `notifier.py` | SMTP email sent on events |
| 16 | S3 driver | `storage/s3.py` | Upload/download/list work with MinIO |
| 17 | SFTP driver | `storage/sftp.py` | Upload/download work via SSH |
| 18 | Restore endpoint | `restore.py`, `api/backups.py` | Stack re-deployed from bundle |
| 19 | Settings UI | `settings.html` | Test connection button works |
| 20 | README | `README.md` | All setup steps documented |

---

## 10. Error Handling Requirements

The AI must implement the following error handling in every relevant module. Incomplete error handling is a defect.

### 10.1 Custom Exception Classes

Define all custom exceptions in `app/exceptions.py`:

```python
class PortainerAuthError(Exception): pass
class PortainerStackNotFoundError(Exception): pass
class PortainerConnectionError(Exception): pass
class DockerSocketError(Exception): pass
class StorageFullError(Exception): pass
class S3AuthError(Exception): pass
class S3BucketError(Exception): pass
class SFTPConnectionError(Exception): pass
```

### 10.2 Portainer API Errors

- `401 Unauthorized` → raise `PortainerAuthError("Check PORTAINER_API_TOKEN")`
- `404 Not Found` → raise `PortainerStackNotFoundError`
- Connection refused → raise `PortainerConnectionError` with the URL
- All errors must be caught in `engine.py` and stored in `job.error_message`

### 10.3 Docker Socket Errors

- Socket not found → raise `DockerSocketError("Is /var/run/docker.sock mounted?")`
- Volume not found → log warning and skip (do not abort the entire backup)
- Container cleanup failure → log error but do not re-raise (always clean up)

### 10.4 Storage Errors

- Local disk full → raise `StorageFullError`
- S3 credentials invalid → raise `S3AuthError`
- S3 bucket not found → raise `S3BucketError`
- SFTP connection refused → raise `SFTPConnectionError`

### 10.5 API Error Response Format

All API errors must return consistent JSON with HTTP status codes:

```json
{
  "error": "Human readable message shown to user",
  "code": "MACHINE_READABLE_CODE",
  "detail": "Optional server-side detail for debugging (logged, not shown in UI)"
}
```

> **Note:** The frontend must display the `error.error` field in red text below the relevant button when a backup fails. Never show a raw stack trace to the user — log it server-side only.

---

## 11. Testing Requirements

### 11.1 Manual Testing Checklist — Phase 1

Verify all of these work before declaring Phase 1 complete:

- [ ] `docker compose up` builds without errors
- [ ] `GET http://localhost:8765` loads the dashboard
- [ ] `GET /api/stacks` returns a JSON list
- [ ] **Backup Now** button creates a job and shows `pending` status
- [ ] Job status transitions to `success` or `failed`
- [ ] On success: `.tar.gz` file exists in the `/backups` Docker volume
- [ ] On success: `manifest.json` inside bundle is valid JSON with correct schema
- [ ] **Download** button returns the `.tar.gz` file
- [ ] **Delete** backup removes both the file and the DB record
- [ ] Unauthenticated API request returns `401`

### 11.2 Manual Testing Checklist — Phase 2

- [ ] Creating a schedule appears in APScheduler and survives container restart
- [ ] Scheduled backup fires at the correct time
- [ ] Backups older than `retention_days` are deleted after a scheduled run
- [ ] Slack notification received on backup success
- [ ] Slack notification received on backup failure
- [ ] S3 upload places file at correct key with correct prefix
- [ ] SFTP upload places file in correct remote directory
- [ ] Restore endpoint re-creates the stack in Portainer

### 11.3 Portainer API Connectivity Test

Before starting implementation, verify the Portainer API token works:

```bash
curl -H "X-API-Key: YOUR_TOKEN" http://portainer:9000/api/stacks
# Expected: JSON array of stacks (may be empty)
```

### 11.4 Volume Export Test

Verify the volume export mechanism works standalone:

```python
import docker
client = docker.from_env()

# This should work without errors
container = client.containers.run(
    "alpine", "true",
    volumes={"your_volume_name": {"bind": "/data", "mode": "ro"}},
    detach=True,
    auto_remove=False
)
bits, stat = container.get_archive("/data")
container.remove(force=True)
print(f"Volume exported: {stat}")
```

---

## Appendix — Portainer API Reference

Key Portainer API endpoints used by this app (Portainer CE and BE compatible):

| Method | Endpoint | Notes |
|---|---|---|
| `GET` | `/api/stacks` | List all stacks |
| `GET` | `/api/stacks/{id}` | Stack metadata including `Env` array |
| `GET` | `/api/stacks/{id}/file` | Raw `docker-compose.yml` content |
| `POST` | `/api/stacks` | Create/redeploy a stack (used by restore) |
| `GET` | `/api/endpoints` | List Docker endpoints |
| `GET` | `/api/endpoints/{id}/docker/volumes` | List volumes for an endpoint |

Authentication header: `X-API-Key: {PORTAINER_API_TOKEN}`

---

*End of Implementation Plan — Portainer Backup Companion v1.0*
