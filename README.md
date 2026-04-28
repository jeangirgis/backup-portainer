# Portainer Backup Companion

A lightweight, self-hosted companion for Portainer that provides automated and on-demand backups for your Docker stacks and volumes.

## Features

- **Stack Backups:** Export `docker-compose.yml` and environment variables directly from the Portainer API.
- **Volume Backups:** Automatically tar and compress Docker volumes attached to your stacks.
- **Scheduling:** Flexible cron-based schedules for automated backups.
- **Retention:** Automatic cleanup of old backups based on a configurable retention period.
- **Storage Backends:** Support for Local Disk, S3-compatible storage (MinIO, AWS, R2), SFTP, and Google Drive.
- **Notifications:** Get notified via Telegram, Slack, Email (SMTP), or Webhooks on backup success or failure.
- **HTMX Dashboard:** A modern, responsive UI with no complex frontend build required.

## Quick Start

### 1. Deploy via Portainer (Git Repository)

Adding Portainer Backup Companion to your Portainer instance via a Git repository is the recommended approach for easy updates. Follow these simple steps:

1. **Log in** to your Portainer dashboard and select your environment (e.g., "local").
2. Navigate to **Stacks** in the left-hand menu.
3. Click the **+ Add stack** button in the top right corner.
4. Enter a name for the stack (e.g., `backup-companion`).
5. Select the **Repository** build method.
6. In the **Repository URL** field, enter the URL of this repository:
   `https://github.com/jeangirgis/backup-portainer.git`
7. Ensure the **Compose path** is set to `docker-compose.yml`.
8. Scroll down to the **Environment variables** section and click **Add environment variable** to configure the required settings:
   - `PORTAINER_URL`: The URL of your Portainer instance (e.g., `http://portainer:9000`)
   - `PORTAINER_API_TOKEN`: Your generated Portainer API token
   - `SECRET_KEY`: A secure random string for authenticating to the companion dashboard
9. Click the **Deploy the stack** button at the bottom of the page.

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
