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
   - `STORAGE_BACKEND`: The storage method to use (e.g., `local`)
   - `LOCAL_BACKUP_DIR`: `/backups`
9. Click the **Deploy the stack** button at the bottom of the page.

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
*   **Important (How Volumes Work):** Docker containers are temporary. If you restart, update, or delete the container, any files saved inside it are destroyed. To prevent losing your backups, you must map a "Volume" from your actual server to the `/backups` folder inside the container. If you used the `docker-compose.yml` from this repository, it automatically creates a volume called `backup_data` and maps it securely to `/backups`, keeping your files safe on your server's hard drive.

    **Want to save backups to a specific folder on your server?**
    Run this script on your server to create a dedicated backup folder and give Docker permission to write to it:
    ```bash
    mkdir -p /opt/portainer-backups
    chmod 777 /opt/portainer-backups
    ```
    Then, in your Portainer stack editor, change the `volumes:` section to point to your new folder:
    ```yaml
    volumes:
      - /opt/portainer-backups:/backups
    ```

#### Option 2: Google Drive
Set `STORAGE_BACKEND=gdrive`

> ⚠️ **CRITICAL: You MUST use a Shared Drive (Google Workspace) — NOT a regular folder!**
> Google Service Accounts have **0 bytes of personal storage quota**. If you upload to a regular Google Drive folder (even one shared with the service account), the upload will fail with the error: `"Service Accounts do not have storage quota"`. The only way to make this work is by uploading to a **Shared Drive** (formerly called Team Drive), which uses the organization's storage pool instead of the service account's personal (empty) quota.

---

##### Understanding the Problem

When you create a Google Service Account, Google gives it its own "personal" Drive space — but with **zero storage**. This means:

| Scenario | Works? | Why |
|----------|--------|-----|
| Upload to a **regular folder** shared with the service account | ❌ **NO** | The file counts against the service account's quota (0 bytes) |
| Upload to a **Shared Drive** where the service account is a member | ✅ **YES** | The file counts against the Shared Drive's pool (your org's storage) |
| Test Connection to a regular folder | ✅ YES (misleading!) | Testing only checks folder access, not upload quota |

This is why "Test Connection" can show ✅ success but backups still fail — the test only verifies the service account can *see* the folder, not that it can *write* files to it.

The exact error you'll see in the backup History if this is misconfigured:
```
HttpError 403: Service Accounts do not have storage quota.
Leverage shared drives (https://developers.google.com/workspace/drive/api/guides/about-shareddrives),
or use OAuth delegation instead.
Details: [{'domain': 'usageLimits', 'reason': 'storageQuotaExceeded'}]
```

---

##### Account Requirements

| Account Type | Shared Drives Available? | Can Use Google Drive Backup? |
|---|---|---|
| **Google Workspace** (Business, Enterprise, Education) | ✅ Yes | ✅ Yes — follow the steps below |
| **Free personal Gmail** (@gmail.com) | ❌ No | ❌ **No** — Shared Drives are a Workspace-only feature. Use Local, S3, or SFTP instead. |

> 💡 **If you only have a free Gmail account**, Google Drive backup will **not work** with a Service Account. Consider using one of the other storage backends (Local Disk, S3/MinIO/R2, or SFTP).

---

##### Step 1: Create a Google Cloud Service Account & JSON Key

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new Project (or select an existing one).
3. Search for **"Google Drive API"** in the top search bar. Click on it and click **Enable**.
4. In the left menu, go to **IAM & Admin → Service Accounts**.
5. Click **+ Create Service Account**.
   - Give it a name (e.g., `portainer-backup`).
   - Click **Done** (no need to grant additional roles).
6. Back on the Service Accounts list, click the **3 dots (⋮)** next to your new account → **Manage keys**.
7. Click **Add Key → Create new key → JSON**.
8. A `.json` file will download to your computer. **Keep this file safe** — you'll paste its contents into the Backup Companion dashboard.
9. **Copy the service account email** (it looks like `your-name@your-project.iam.gserviceaccount.com`). You'll need this in Step 2.

---

##### Step 2: Create a Shared Drive and Add the Service Account

> ⚠️ This step requires a **Google Workspace** account. Shared Drives are NOT available on free Gmail.

1. Open [Google Drive](https://drive.google.com) in your browser (logged in with your Workspace account).
2. In the left sidebar, click **Shared drives**.
3. Click **+ New** (or **"+ Create shared drive"**) at the top left.
4. Name it something like **"Portainer Backups"** and click **Create**.
5. You should now be inside the new Shared Drive. Click the **gear icon (⚙️)** or the Shared Drive name at the top → **Manage members**.
6. In the **"Add people"** field, paste the **service account email** from Step 1.
7. Set the role to **Content Manager** (or **Manager**). This gives the service account permission to upload and delete files.
8. Click **Send** / **Share**.

---

##### Step 3: Get the Shared Drive Folder ID

You can upload to the root of the Shared Drive, or create a subfolder inside it.

**Option A — Use the Shared Drive root:**
1. Open the Shared Drive you just created.
2. Look at the URL in your browser. It looks like:
   ```
   https://drive.google.com/drive/u/0/folders/0ABcDeFgHiJkLmNoPq
   ```
3. The long string after `folders/` is your **Folder ID** (e.g., `0ABcDeFgHiJkLmNoPq`).

**Option B — Use a subfolder inside the Shared Drive:**
1. Inside your Shared Drive, create a new folder (e.g., "Daily Backups").
2. Open that folder and copy the ID from the URL the same way.

> 📝 **Important:** The Folder ID for a Shared Drive root usually starts with `0A...`. Regular folder IDs start with `1...`. If your ID starts with `1`, double-check that it's inside a Shared Drive and not a regular personal folder.

---

##### Step 4: Configure in the Web Dashboard

1. Open the Backup Companion dashboard at `http://your-ip:8765`.
2. Go to **Settings → Storage** tab.
3. Click the **Google Drive** provider card.
4. Paste your **Folder ID** (from Step 3) into the **Google Drive Folder ID** field.
5. Paste the **entire contents** of your downloaded JSON key file into the **Service Account JSON Key** textarea.
6. Click **💾 Save & Apply**.
7. Click **🔌 Test Connection** to verify access.
8. Go to the **Dashboard** and try **Backup Now** on a stack to confirm uploads work end-to-end.

> ⚠️ **"Test Connection" passing does NOT guarantee backups will work!** The test only checks if the service account can access the folder. The actual upload can still fail if the folder is not on a Shared Drive. Always do a real test backup after configuring.

---

##### Troubleshooting Google Drive

| Problem | Cause | Solution |
|---------|-------|----------|
| `storageQuotaExceeded` / "Service Accounts do not have storage quota" | Uploading to a regular folder, not a Shared Drive | Move your folder to a Shared Drive (see Step 2 above) |
| `Test Connection` says "ok" but backups fail | Test only checks access, not upload quota | Do a real backup to test. If it fails with quota error, you need a Shared Drive |
| `404 File not found` when accessing the folder | Folder not shared with the service account, or wrong Folder ID | Re-share the Shared Drive with the service account email (Step 2) |
| `403 Insufficient permissions` | Service account doesn't have write access | Make sure the service account is a **Content Manager** or **Manager** on the Shared Drive |
| `Invalid JSON` when saving credentials | Incomplete or malformed JSON key | Make sure you pasted the ENTIRE JSON file contents, including the opening `{` and closing `}` |
| Backups succeed but files don't appear in Google Drive web UI | Files are in the Shared Drive but you're looking in "My Drive" | Open the **Shared drives** section in the left sidebar of Google Drive |

---

##### Alternative: Environment Variables (Advanced)

Instead of using the web dashboard, you can configure Google Drive via environment variables in your `docker-compose.yml`:

```yaml
environment:
  - STORAGE_BACKEND=gdrive
  - GDRIVE_FOLDER_ID=your_shared_drive_folder_id
  - GDRIVE_CREDENTIALS_FILE=/app/credentials.json
volumes:
  - ./credentials.json:/app/credentials.json:ro
```

Place your downloaded JSON key file as `credentials.json` next to your `docker-compose.yml`.

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
