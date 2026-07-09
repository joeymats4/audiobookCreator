#!/usr/bin/env python3
"""Optional Google Drive integration: connect via OAuth and upload files.

Uses the OAuth 2.0 "installed app" flow with the least-privilege `drive.file`
scope — the app can only see and manage files it creates, nothing else in the
user's Drive. Two credential files live in the project root and are gitignored:
  - client_secret.json : the OAuth client, downloaded from Google Cloud Console
  - token.json         : the cached user authorization, created on first connect

Google libraries are imported lazily so the rest of the app runs fine even if
they aren't installed yet.
"""

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT_SECRET = os.path.join(ROOT, "client_secret.json")
TOKEN = os.path.join(ROOT, "token.json")
SCOPES = ["https://www.googleapis.com/auth/drive.file"]
FOLDER_NAME = "Audiobooks"


class DriveError(Exception):
    """A user-friendly Google Drive problem."""


def is_configured():
    """True once the OAuth client (client_secret.json) is present."""
    return os.path.isfile(CLIENT_SECRET)


def is_connected():
    """True once a user has authorized the app (token.json exists)."""
    return os.path.isfile(TOKEN)


def status():
    return {"configured": is_configured(), "connected": is_connected()}


def connect():
    """Run the browser consent flow and cache the token. Blocks until finished."""
    if not is_configured():
        raise DriveError(
            "No client_secret.json found. In Google Cloud Console: enable the Drive API, "
            "create an OAuth client of type 'Desktop app', download it, and save it as "
            "client_secret.json in the project folder. See the README for the walkthrough."
        )
    _, InstalledAppFlow, _, _, _ = _load_google()
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
    try:
        # Opens the browser on its own random port. timeout_seconds keeps an
        # abandoned consent page from parking a server thread forever.
        creds = flow.run_local_server(port=0, timeout_seconds=300)
    except Exception as e:
        raise DriveError(
            "Google sign-in didn't complete (timed out or was cancelled). Try again."
        ) from e
    _save(creds)
    return True


def disconnect():
    if os.path.isfile(TOKEN):
        os.remove(TOKEN)
    return True


def upload(path, filename):
    """Upload `path` into the app's Audiobooks folder; return its shareable link."""
    _, _, _, build, MediaFileUpload = _load_google()
    creds = _credentials()
    if creds is None:
        raise DriveError("Google Drive isn't connected. Click 'Connect Google Drive' first.")
    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    folder_id = _ensure_folder(service)
    media = MediaFileUpload(path, resumable=True)
    meta = {"name": filename, "parents": [folder_id]}
    created = service.files().create(
        body=meta, media_body=media, fields="id, webViewLink"
    ).execute()
    return created.get("webViewLink")


# --------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------
def _load_google():
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError as e:
        raise DriveError(
            "Google Drive libraries aren't installed. Run: pip install -r requirements.txt"
        ) from e
    return Credentials, InstalledAppFlow, Request, build, MediaFileUpload


def _credentials():
    """Return valid (refreshed) credentials, or None if not connected."""
    Credentials, _, Request, _, _ = _load_google()
    if not os.path.isfile(TOKEN):
        return None
    creds = Credentials.from_authorized_user_file(TOKEN, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save(creds)
    return creds if creds and creds.valid else None


def _save(creds):
    with open(TOKEN, "w", encoding="utf-8") as f:
        f.write(creds.to_json())


def _ensure_folder(service):
    """Find (or create) the app's Audiobooks folder; return its id.

    With the drive.file scope, list() only sees folders this app created, which
    is exactly what we want.
    """
    q = (
        "mimeType='application/vnd.google-apps.folder' and trashed=false "
        f"and name='{FOLDER_NAME}'"
    )
    res = service.files().list(q=q, spaces="drive", fields="files(id, name)").execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    folder = service.files().create(
        body={"name": FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"},
        fields="id",
    ).execute()
    return folder["id"]
