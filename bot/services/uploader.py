import asyncio
from pathlib import Path
from typing import Awaitable, Callable, Optional
from urllib.parse import urlparse

import httplib2
import google_auth_httplib2
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from loguru import logger

from bot.config import config

SCOPES = ["https://www.googleapis.com/auth/drive"]
ProgressCallback = Callable[..., Awaitable[None]]

_TIMEOUT = 300  # 5 minutes


# ─── Drive helpers (sync — run inside executor) ───────────────────────────────

def _build_service():
    creds = service_account.Credentials.from_service_account_file(
        config.SERVICE_ACCOUNT_PATH, scopes=SCOPES
    )
    if config.PROXY_URL:
        p = urlparse(config.PROXY_URL)
        proxy_info = httplib2.ProxyInfo(
            proxy_type=httplib2.socks.PROXY_TYPE_HTTP,
            proxy_host=p.hostname,
            proxy_port=p.port or 80,
            proxy_user=p.username,
            proxy_pass=p.password,
        )
        http = httplib2.Http(proxy_info=proxy_info, timeout=_TIMEOUT)
    else:
        http = httplib2.Http(timeout=_TIMEOUT)
    authorized_http = google_auth_httplib2.AuthorizedHttp(creds, http=http)
    return build("drive", "v3", http=authorized_http, cache_discovery=False)


def _get_or_create_folder(service, name: str, parent_id: str) -> str:
    """Return the ID of an existing sub-folder or create it under *parent_id*."""
    safe_name = name.replace("'", "\\'")
    query = (
        f"name='{safe_name}' "
        f"and mimeType='application/vnd.google-apps.folder' "
        f"and '{parent_id}' in parents "
        f"and trashed=false"
    )
    res = service.files().list(q=query, fields="files(id)").execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]

    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    folder = service.files().create(body=metadata, fields="id").execute()
    logger.info("Created Drive folder '{}' ({})", name, folder["id"])
    return folder["id"]


def _upload_file(
    service,
    file_path: str,
    folder_id: str,
    loop: asyncio.AbstractEventLoop,
    progress_cb: Optional[ProgressCallback],
) -> dict:
    file_name = Path(file_path).name
    file_size = Path(file_path).stat().st_size

    metadata = {"name": file_name, "parents": [folder_id]}
    media = MediaFileUpload(
        file_path,
        mimetype="video/mp4",
        resumable=True,
        chunksize=5 * 1024 * 1024,  # 5 MB chunks
    )

    request = service.files().create(
        body=metadata,
        media_body=media,
        fields="id,webViewLink",
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status and progress_cb:
            uploaded = status.resumable_progress
            percent = int(uploaded / file_size * 100) if file_size else 0
            asyncio.run_coroutine_threadsafe(
                progress_cb(percent=percent, uploaded=uploaded, total=file_size),
                loop,
            )

    # Make file viewable by anyone with the link
    service.permissions().create(
        fileId=response["id"],
        body={"type": "anyone", "role": "reader"},
    ).execute()

    link = response.get(
        "webViewLink",
        f"https://drive.google.com/file/d/{response['id']}/view",
    )
    logger.info("Uploaded '{}' → {}", file_name, link)
    return {"file_id": response["id"], "link": link}


# ─── Public async API ─────────────────────────────────────────────────────────

async def upload_to_drive(
    file_path: str,
    user_chat_id: int,
    progress_cb: Optional[ProgressCallback] = None,
) -> dict:
    """
    Upload *file_path* to a per-user subfolder on Google Drive.

    The subfolder is named after the user's chat ID and created automatically
    inside the root folder specified by ``DRIVE_FOLDER_ID``.

    Returns ``{"file_id": ..., "link": ...}``.
    """
    loop = asyncio.get_event_loop()

    def _run() -> dict:
        service = _build_service()
        folder_id = _get_or_create_folder(
            service, str(user_chat_id), config.DRIVE_FOLDER_ID
        )
        return _upload_file(service, file_path, folder_id, loop, progress_cb)

    return await loop.run_in_executor(None, _run)
