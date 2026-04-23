# Portainer Backup Companion

A lightweight, self-hosted companion for Portainer that provides automated and on-demand backups for your Docker stacks and volumes.

## Features

- **Stack Backups:** Export `docker-compose.yml` and environment variables directly from the Portainer API.
- **Volume Backups:** Automatically tar and compress Docker volumes attached to your stacks.
- **Scheduling:** Flexible cron-based schedules for automated backups.
- **Retention:** Automatic cleanup of old backups based on a configurable retention period.
- **Storage Backends:** Support for Local Disk, S3-compatible storage (MinIO, AWS, R2), and SFTP.
- **Notifications:** Get notified via Slack, Email, or Webhooks on backup success or failure.
- **HTMX Dashboard:** A modern, responsive UI with no complex frontend build required.

## Quick Start

### 1. Deploy via Portainer

Create a new stack in Portainer and paste the following `docker-compose.yml`:

```yaml
version: '3.8'

services:
  backup-companion:
    image: portainer-backup-companion:latest
    container_name: portainer-backup-companion
    restart: unless-stopped
    ports:
      - "8765:8000"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - backup_data:/backups
    environment:
      - PORTAINER_URL=http://portainer:9000
      - PORTAINER_API_TOKEN=your_token_here
      - SECRET_KEY=your_secret_key_here
      - STORAGE_BACKEND=local
      - LOCAL_BACKUP_DIR=/backups

volumes:
  backup_data:
```

### 2. Configuration

Set the following environment variables:

| Variable | Description |
|---|---|
| `PORTAINER_URL` | The URL of your Portainer instance. |
| `PORTAINER_API_TOKEN` | API Token generated in Portainer User Settings. |
| `SECRET_KEY` | A random string used to authenticate the dashboard. |

### 3. Usage

1. Access the dashboard at `http://your-ip:8765`.
2. Enter your `SECRET_KEY` when prompted.
3. Click **Backup Now** on any stack to trigger an immediate backup.
4. Go to the **Schedules** tab to set up recurring backups.

## Development

To run locally:

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Ensure you have a `.env` file with the required variables.

## License

MIT
