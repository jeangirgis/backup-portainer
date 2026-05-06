# Backtainer

A lightweight, self-hosted companion for Portainer that provides automated and on-demand backups for your Docker stacks and volumes, complete with a beautiful UI.

## ✨ Features

- **Automated Stack Backups:** Export `docker-compose.yml` and environment variables directly from the Portainer API.
- **Automated Volume Backups:** Automatically discover, tar, and compress Docker volumes attached to your stacks without manual mapping.
- **1-Click Stack Restore:** Instantly roll back any stack. Backtainer unpacks the volumes, stops the containers, injects the volume data, updates the `docker-compose.yml` in Portainer, and brings the stack back up.
- **Universal Notifications (Apprise):** Get alerted on backup success or failure across 90+ platforms (Discord, Telegram, Slack, Matrix, Email, etc.) using simple Apprise URLs.
- **Job Logs Viewer:** Monitor your backup jobs in real-time with an integrated, terminal-style logs viewer right in your browser.
- **Flexible Scheduling:** Set up cron-based schedules for hands-free, automated backups. Edit existing schedules on the fly to change frequency or retention policies without recreating them.
- **Smart Retention:** Automatic cleanup of old backups based on a configurable retention period to save space.
- **Multiple Storage Backends:** Store backups locally, securely in S3-compatible storage (AWS, MinIO, Cloudflare R2), SFTP, or Google Drive (Shared Drives).
- **Modern UI:** A stunning HTMX-powered dashboard with Dark and Light modes.

## 📸 Screenshots

| Dashboard | History |
|---|---|
| ![Dashboard](assets/dashboard.png) | ![History](assets/history.png) |

| Schedules | Logs |
|---|---|
| ![Schedules](assets/schedules.png) | ![Logs](assets/logs.png) |

| Settings (General) | Settings (Storage) | Settings (Notifications) |
|---|---|---|
| ![Settings General](assets/settings-general.png) | ![Settings Storage](assets/settings-storage.png) | ![Settings Notifications](assets/settings-notifications.png) |

---

---

## 🚀 Quick Start Tutorial

### Step 1: Deploy Backtainer via Portainer

Adding Backtainer to your Portainer instance via a Git repository is the recommended approach.

1. **Log in** to your Portainer dashboard and select your local environment.
2. Navigate to **Stacks** in the left-hand menu and click **+ Add stack**.
3. Enter a name for the stack (e.g., `backup-companion`).
4. Select the **Repository** build method.
5. In the **Repository URL** field, enter:
   `https://github.com/jeangirgis/backup-portainer.git`
6. Ensure the **Compose path** is set to `docker-compose.yml`.
7. In the **Environment variables** section, add the required settings:
   - `PORTAINER_URL`: The URL of your Portainer instance (e.g., `http://portainer:9000`)
   - `PORTAINER_API_TOKEN`: Your generated Portainer API token (See Step 2 below)
   - `SECRET_KEY`: A secure random password for logging into the Backtainer dashboard
   - `STORAGE_BACKEND`: `local` (You can change this later in the UI)
   - `LOCAL_BACKUP_DIR`: `/backups`
8. Click **Deploy the stack**.

### Step 2: Generate a Portainer API Token

Backtainer needs permission to talk to Portainer.

1. Click your username in the top right corner of Portainer -> **My account**.
2. Scroll down to **API tokens**.
3. Click **Add token**, name it "Backtainer", and copy the long string. Paste this into your `PORTAINER_API_TOKEN` environment variable.

### Step 3: Access the Dashboard

1. Navigate to `http://your-server-ip:8765` in your browser.
2. Enter the `SECRET_KEY` you defined during setup.
3. You will see a list of all your active Portainer stacks!

---

## 📖 How to Use Backtainer

### Backing Up a Stack

- On the **Dashboard**, click the **Backup Now** button next to any stack.
- Backtainer will automatically find all associated containers, map out their volumes, compress the data, and bundle it with your `docker-compose.yml` into a `.tar.gz` file.
- You can monitor the progress in real-time by clicking the **Logs** tab in the navigation bar.

### Scheduling Automated Backups

- Go to the **Schedules** tab.
- Select a stack, set a cron expression (e.g., `0 2 * * *` for daily at 2 AM), and click **Save Schedule**.
- Backtainer will wake up automatically and run the backup in the background.
- You can easily edit existing schedules to change the frequency or retention policy without having to recreate them.

### 1-Click Restore

Disaster struck? Fixing it takes seconds.

1. Go to the **History** tab.
2. Find the successful backup you want to restore and click **Restore**.
3. **What happens under the hood:** Backtainer stops the active containers, securely wipes and replaces the volume data with the backup, updates the Stack configuration in Portainer (in case you changed the compose file), and restarts the containers automatically!

---

## ⚙️ Configuration Guide

While you can set most options in your `docker-compose.yml`, Backtainer features a **Settings** tab where you can configure storage and notifications without restarting the container!

### 💾 Storage Backends

You can save your backups to multiple destinations.

#### Option 1: Local Disk (Default)

By default, backups are saved inside the container at `/backups`.
**Important:** To prevent losing your backups if the container is destroyed, you must map a volume from your host machine to `/backups` in your `docker-compose.yml`.

```yaml
volumes:
  - /opt/portainer-backups:/backups
```

#### Option 2: S3 / AWS / MinIO / Cloudflare R2

Go to **Settings > Storage**, select S3, and enter your Bucket Name, Access Key, Secret Key, Region, and Endpoint URL.

#### Option 3: SFTP

Go to **Settings > Storage**, select SFTP, and enter your server IP, Port (usually 22), Username, Password, and the Remote Directory path.

#### Option 4: Google Drive (Shared Drives ONLY)
>
> ⚠️ **CRITICAL:** You MUST use a Google Workspace **Shared Drive**. Regular personal Google Drive folders will not work due to Service Account quota limits (0 bytes).

1. Go to [Google Cloud Console](https://console.cloud.google.com/), enable the [Google Drive API](https://console.cloud.google.com/apis/library/drive.googleapis.com), and create a [Service Account](https://console.cloud.google.com/iam-admin/serviceaccounts).
2. Download the JSON Key file.
3. In Google Drive, create a **Shared Drive** and share it with your Service Account email as a **Content Manager**.
4. Copy the Folder ID from the URL (e.g., `0ABcDeFgHiJkLmNoPq`).
5. In Backtainer's **Settings > Storage**, paste the Folder ID and the entire JSON Key. Click **Save & Apply**.

---

## 🔔 Universal Notifications (Apprise)

Backtainer uses the powerful [Apprise](https://github.com/caronc/apprise/wiki) library, allowing you to send backup success/failure alerts to over 90 different platforms simultaneously!

1. Go to **Settings > Notifications**.
2. Expand the **Apprise (Universal)** section.
3. Enter your Apprise URLs separated by commas. Examples:
   - **Discord:** `discord://webhook_id/webhook_token`
   - **Telegram:** `tgram://bot_token/chat_id`
   - **Slack:** `slack://TokenA/TokenB/TokenC/Channel`
   - **Email:** `mailto://user:pass@gmail.com`

   > 💡 **Gmail Note:** Google blocks regular passwords for third-party apps. You must generate an **App Password** in your Google Account (under 2-Step Verification) and use that 16-character code as your password in the URL. Make sure to remove any spaces from the App Password! If your email is `user@gmail.com`, your URL should be formatted exactly like: `mailto://user:16charpassword@gmail.com`.

4. Click **Send Test** to verify your setup, then click **Save All Notifications**.

> 📖 **More Info:** For a full list of supported services and their URL formats, visit the [Apprise Documentation](https://github.com/caronc/apprise/wiki).

---

## 🎨 Personalization

Click the **🌓 Theme** button in the top right corner to toggle between Dark Mode (default) and Light Mode to suit your preference!

---
**Backtainer v2.5.0** — Taking the stress out of self-hosting.
