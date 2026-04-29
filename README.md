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

---

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

### 2. Usage

1. Access the dashboard at `http://your-ip:8765`.
2. Enter your `SECRET_KEY` when prompted.
3. Click **Backup Now** on any stack to trigger an immediate backup.
4. Go to the **Schedules** tab to set up recurring backups.

---

## 📖 Configuration Guide (Step-by-Step for Dummies)

You can configure the application by setting **Environment Variables** in your Portainer stack definition.

### 🔑 Core Requirements

You MUST set these variables for the application to run.

*   `PORTAINER_URL`: The full URL to your Portainer instance (e.g., `http://192.168.1.100:9000`).
*   `SECRET_KEY`: Make up a random password. You will use this to log into the Backup Companion web dashboard.
*   `PORTAINER_API_TOKEN`:
    *   **How to get it:** Log into Portainer. Click your username in the top right -> **My account**. Scroll down to **API tokens**. Click **Add token**, give it a name (like "Backup App"), and copy the long string it gives you.

---

### 💾 Storage Backends

First, set `STORAGE_BACKEND` to one of the following: `local`, `s3`, `sftp`, or `gdrive`. Then configure the specific settings for your chosen backend.

#### Option 1: Local Disk (Default)
Set `STORAGE_BACKEND=local`
*   No extra configuration needed. Backups will be stored inside the container at `/backups`.
*   **Important:** Make sure you mount a volume to `/backups` in your `docker-compose.yml` so you don't lose the files if the container stops!

#### Option 2: Google Drive
Set `STORAGE_BACKEND=gdrive`
*   **The Easy Way:** You do not need to set environment variables for credentials. Just open the Backup Companion Web Dashboard, go to **Settings**, and paste your credentials in the Google Drive Configuration form!
*   **How to get the credentials:**
    1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
    2. Create a new Project (or select an existing one) and search for **Google Drive API** at the top. Click **Enable**.
    3. Go to **IAM & Admin > Service Accounts** in the left menu.
    4. Click **Create Service Account**, name it, and click Done.
    5. Click the 3 dots next to the new service account -> **Manage keys**.
    6. Click **Add Key -> Create new key -> JSON**. This will download a file to your computer. You will paste the contents of this file into the Web Dashboard.
*   **How to get the Folder ID:**
    1. Open your Google Drive and create a new folder (e.g., "Portainer Backups").
    2. Right-click the folder and click **Share**. Share it with the email address of the Service Account you just created (give it "Editor" access).
    3. Look at the URL in your browser. It looks like `https://drive.google.com/drive/folders/1A2B3C4D5E6F7G8H9I0J`. The long random string at the end is your Folder ID.

#### Option 3: S3 / AWS / MinIO / Cloudflare R2
Set `STORAGE_BACKEND=s3`
*   `S3_BUCKET`: The name of your bucket.
*   `S3_ACCESS_KEY` & `S3_SECRET_KEY`:
    *   **How to get them (AWS):** Go to AWS IAM -> Users -> Create User -> Create Access Key.
    *   **How to get them (Cloudflare R2):** Go to Cloudflare Dashboard -> R2 -> Manage R2 API Tokens -> Create API token.
*   `S3_REGION`: E.g., `us-east-1` or `auto`.
*   `S3_ENDPOINT_URL`: (Optional) Leave empty for AWS. For Cloudflare R2, it looks like `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`. For MinIO, it's your local URL like `http://192.168.1.100:9000`.
*   `S3_PREFIX`: (Optional) Folder path inside the bucket, e.g., `backups/`.

#### Option 4: SFTP
Set `STORAGE_BACKEND=sftp`
*   `SFTP_HOST`: IP or domain of the SSH server.
*   `SFTP_PORT`: Usually `22`.
*   `SFTP_USER`: Your SSH username.
*   `SFTP_PASSWORD`: Your SSH password.
*   `SFTP_REMOTE_DIR`: Folder on the remote server to put files (e.g., `/home/user/backups`).

---

### 🔔 Notifications

You can enable one or more notifications to be alerted when a backup succeeds or fails.

#### Option 1: Telegram
*   `TELEGRAM_BOT_TOKEN`:
    *   **How to get it:** Open Telegram and search for `@BotFather`. Send the message `/newbot`. Follow the steps to name your bot. BotFather will give you a token that looks like `123456789:ABCdefGHIjklmNOPqrsTUVwxyz`.
*   `TELEGRAM_CHAT_ID`:
    *   **How to get it:** Send a message to your new bot. Then search Telegram for `@userinfobot` or `@getmyid_bot` and send a message. It will reply with your Chat ID (a number like `12345678`).

#### Option 2: Slack
*   `NOTIFY_SLACK_WEBHOOK`:
    *   **How to get it:** Go to your Slack Workspace settings -> Apps -> Build. Create a new App from scratch. Go to **Incoming Webhooks**, turn it On, and click **Add New Webhook to Workspace**. Choose a channel and copy the Webhook URL.

#### Option 3: Email (SMTP)
*   `NOTIFY_EMAIL_TO`: The email address to send the alert TO.
*   `NOTIFY_EMAIL_FROM`: The email address sending the alert.
*   `SMTP_HOST`: e.g., `smtp.gmail.com`
*   `SMTP_PORT`: Usually `587`
*   `SMTP_USER`: Your email address.
*   `SMTP_PASSWORD`:
    *   **For Gmail:** You cannot use your normal password. Go to your Google Account -> Security -> 2-Step Verification -> **App Passwords**. Create a new App Password and paste that 16-letter code here.

#### Option 4: Generic Webhook
*   `NOTIFY_WEBHOOK_URL`: A custom URL to send a POST request to (useful for n8n, Make, Zapier, or custom APIs).
