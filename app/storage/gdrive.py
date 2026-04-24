import os
import logging
import io
from pathlib import Path
from typing import List, Dict
from datetime import datetime
from app.storage.base import StorageDriver

logger = logging.getLogger(__name__)

class GoogleDriveDriver(StorageDriver):
    def __init__(self, credentials_file: Path, folder_id: str):
        self.credentials_file = credentials_file
        self.folder_id = folder_id
        self._service = None
        
        if not self.credentials_file.exists():
            raise FileNotFoundError(f"Google Drive credentials file not found at {self.credentials_file}")

    @property
    def service(self):
        if self._service is None:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
            
            SCOPES = ['https://www.googleapis.com/auth/drive']
            creds = service_account.Credentials.from_service_account_file(
                str(self.credentials_file), scopes=SCOPES)
            self._service = build('drive', 'v3', credentials=creds, cache_discovery=False)
        return self._service

    async def upload(self, local_path: Path, remote_name: str) -> str:
        """Upload file to Google Drive folder. Returns the file ID."""
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_upload, local_path, remote_name)

    def _sync_upload(self, local_path: Path, remote_name: str) -> str:
        from googleapiclient.http import MediaFileUpload
        
        file_metadata = {
            'name': remote_name,
            'parents': [self.folder_id]
        }
        media = MediaFileUpload(str(local_path), resumable=True)
        
        # Check if file with same name already exists to overwrite/delete it first
        existing_file_id = self._find_file_by_name(remote_name)
        
        if existing_file_id:
            logger.info(f"Updating existing file in Google Drive: {remote_name} ({existing_file_id})")
            file = self.service.files().update(
                fileId=existing_file_id,
                media_body=media
            ).execute()
        else:
            logger.info(f"Uploading new file to Google Drive: {remote_name}")
            file = self.service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id'
            ).execute()
            
        return file.get('id')

    async def download(self, remote_path: str, local_path: Path) -> None:
        """Download file from Google Drive. remote_path is the file ID."""
        import asyncio
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_download, remote_path, local_path)

    def _sync_download(self, file_id: str, local_path: Path) -> None:
        from googleapiclient.http import MediaIoBaseDownload
        
        request = self.service.files().get_media(fileId=file_id)
        with open(local_path, 'wb') as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()
                if status:
                    logger.debug(f"Download {int(status.progress() * 100)}%.")

    async def delete(self, remote_path: str) -> None:
        """Delete file from Google Drive by ID."""
        import asyncio
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_delete, remote_path)

    def _sync_delete(self, file_id: str) -> None:
        try:
            self.service.files().delete(fileId=file_id).execute()
        except Exception as e:
            logger.error(f"Failed to delete file {file_id} from Google Drive: {e}")

    async def list_backups(self) -> List[Dict]:
        """List backup files in the Google Drive folder."""
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_list_backups)

    def _sync_list_backups(self) -> List[Dict]:
        backups = []
        try:
            # Query files in the specific folder
            query = f"'{self.folder_id}' in parents and trashed = false"
            results = self.service.files().list(
                q=query,
                pageSize=100,
                fields="nextPageToken, files(id, name, size, modifiedTime)"
            ).execute()
            
            items = results.get('files', [])
            
            for item in items:
                # modifiedTime format: 2023-10-25T12:00:00.000Z
                mod_time_str = item.get('modifiedTime')
                mod_time = datetime.now()
                if mod_time_str:
                    try:
                        # Parse RFC 3339 timestamp
                        mod_time_str = mod_time_str.replace('Z', '+00:00')
                        mod_time = datetime.fromisoformat(mod_time_str)
                    except Exception:
                        pass
                
                size = int(item.get('size', 0))
                
                backups.append({
                    "name": item.get('name'),
                    "id": item.get('id'), # The ID is the "storage_path" in our db
                    "size": size,
                    "modified_at": mod_time
                })
        except Exception as e:
            logger.error(f"Failed to list backups from Google Drive: {e}")
            
        return backups
        
    def _find_file_by_name(self, name: str) -> str:
        """Helper to find a file ID by its name in the specific folder."""
        query = f"'{self.folder_id}' in parents and name = '{name}' and trashed = false"
        results = self.service.files().list(
            q=query,
            pageSize=1,
            fields="files(id)"
        ).execute()
        items = results.get('files', [])
        if items:
            return items[0]['id']
        return None
