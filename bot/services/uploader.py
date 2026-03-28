import asyncio
import mimetypes
import threading
from pathlib import Path
from typing import Awaitable, Callable, Optional

import requests
from google.auth.transport.requests import AuthorizedSession
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from loguru import logger

from bot.config import config

SCOPES = ["https://www.googleapis.com/auth/drive"]
ProgressCallback = Callable[..., Awaitable[None]]

_TIMEOUT = 300
_CHUNK = 5 * 1024 * 1024  # 5 MB chunks
_DRIVE_API = "https://www.googleapis.com/drive/v3"
_UPLOAD_API = "https://www.googleapis.com/upload/drive/v3/files"

# Prevents concurrent threads from corrupting token.json during refresh
_token_lock = threading.Lock()


# ─── Session factory ──────────────────────────────────────────────────────────

def _make_session() -> AuthorizedSession:
    proxies = (
        {"http": config.PROXY_URL, "https": config.PROXY_URL}
        if config.PROXY_URL else {}
    )
    refresh_session = requests.Session()
    if proxies:
        refresh_session.proxies.update(proxies)

    with _token_lock:
        creds = Credentials.from_authorized_user_file(config.OAUTH_TOKEN_PATH, scopes=SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(GoogleAuthRequest(session=refresh_session))
            Path(config.OAUTH_TOKEN_PATH).write_text(creds.to_json())
            logger.debug("OAuth token refreshed and persisted")

    session = AuthorizedSession(creds, auth_request=GoogleAuthRequest(session=refresh_session))
    if proxies:
        session.proxies.update(proxies)
    return session


# ─── Drive helpers (sync — run inside executor) ───────────────────────────────

def _get_or_create_folder(session: AuthorizedSession, name: str, parent_id: str) -> str:
    """Return the ID of an existing sub-folder or create it under *parent_id*."""
    # Use parameterized-style quoting: only single-quote the name, nothing else
    safe_name = name.replace("\\", "\\\\").replace("'", "\\'")
    query = (
        f"name='{safe_name}' "
        f"and mimeType='application/vnd.google-apps.folder' "
        f"and '{parent_id}' in parents "
        f"and trashed=false"
    )
    resp = session.get(
        f"{_DRIVE_API}/files",
        params={"q": query, "fields": "files(id)"},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    files = resp.json().get("files", [])
    if files:
        return files[0]["id"]

    resp = session.post(
        f"{_DRIVE_API}/files",
        json={
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        },
        params={"fields": "id"},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    folder_id = resp.json()["id"]
    logger.info("Created Drive folder '{}' ({})", name, folder_id)
    return folder_id


def _upload_file(
    session: AuthorizedSession,
    file_path: str,
    folder_id: str,
    loop: asyncio.AbstractEventLoop,
    progress_cb: Optional[ProgressCallback],
) -> dict:
    file_name = Path(file_path).name
    file_size = Path(file_path).stat().st_size

    # Detect MIME type from file extension; fall back to octet-stream
    mime_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"

    # Initiate resumable upload session
    init_resp = session.post(
        _UPLOAD_API,
        params={"uploadType": "resumable", "fields": "id,webViewLink"},
        json={"name": file_name, "parents": [folder_id]},
        headers={
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": mime_type,
            "X-Upload-Content-Length": str(file_size),
        },
        timeout=_TIMEOUT,
    )
    if not init_resp.ok:
        logger.error("Drive upload init failed {}: {}", init_resp.status_code, init_resp.text)
    init_resp.raise_for_status()
    upload_uri = init_resp.headers["Location"]

    # Upload chunks
    uploaded = 0
    file_id = None
    web_view_link = None

    with open(file_path, "rb") as f:
        while uploaded < file_size:
            chunk = f.read(_CHUNK)
            end = uploaded + len(chunk) - 1
            resp = session.put(
                upload_uri,
                data=chunk,
                headers={
                    "Content-Range": f"bytes {uploaded}-{end}/{file_size}",
                    "Content-Type": mime_type,
                },
                timeout=_TIMEOUT,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                file_id = data["id"]
                web_view_link = data.get("webViewLink")
                uploaded = file_size
            elif resp.status_code == 308:
                uploaded = end + 1
                if progress_cb:
                    percent = int(uploaded / file_size * 100)
                    asyncio.run_coroutine_threadsafe(
                        progress_cb(percent=percent, uploaded=uploaded, total=file_size),
                        loop,
                    )
            else:
                resp.raise_for_status()

    # Make file viewable by anyone with the link
    session.post(
        f"{_DRIVE_API}/files/{file_id}/permissions",
        json={"type": "anyone", "role": "reader"},
        timeout=_TIMEOUT,
    ).raise_for_status()

    link = web_view_link or f"https://drive.google.com/file/d/{file_id}/view"
    logger.info("Uploaded '{}' ({}) → {}", file_name, mime_type, link)
    return {"file_id": file_id, "link": link}


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
        session = _make_session()
        folder_id = _get_or_create_folder(session, str(user_chat_id), config.DRIVE_FOLDER_ID)
        return _upload_file(session, file_path, folder_id, loop, progress_cb)

    return await loop.run_in_executor(None, _run)
