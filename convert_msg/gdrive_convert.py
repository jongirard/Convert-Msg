#!/usr/bin/env python3
"""
Convert .msg files from a Google Drive folder using extract-msg.

Recursively traverses a Google Drive folder, downloads all .msg files,
converts them locally, and uploads the converted output folders back to
the same location on Google Drive.

Usage:
    gdrive-convert-msg <FOLDER_URL_OR_ID>
    gdrive-convert-msg <FOLDER_URL_OR_ID> --dry-run
    gdrive-convert-msg <FOLDER_URL_OR_ID> --credentials /path/to/creds.json
"""

import argparse
import io
import mimetypes
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from convert_msg.convert_msg import (
    add_conversion_args,
    build_save_kwargs,
    convert_single_msg,
    kwargs_from_args,
)

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
]

DRIVE_FOLDER_URL_PATTERN = re.compile(
    r"https://drive\.google\.com/drive/(?:u/\d+/)?folders/([a-zA-Z0-9_-]+)"
)

FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"


def authenticate(
    credentials_path: str = "credentials.json",
    token_path: str = "token.json",
) -> Credentials:
    """
    Perform OAuth2 user consent flow for Google Drive access.

    Loads an existing token if valid, refreshes if expired, or launches
    a browser-based consent flow if no token is available.
    """
    creds = None

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None

        if not creds:
            if not os.path.exists(credentials_path):
                print(
                    f"Error: '{credentials_path}' not found.\n"
                    "Download OAuth2 client credentials from Google Cloud Console.\n"
                    "See: https://developers.google.com/drive/api/quickstart/python"
                )
                sys.exit(1)

            flow = InstalledAppFlow.from_client_secrets_file(
                credentials_path, SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open(token_path, "w") as f:
            f.write(creds.to_json())

    return creds


def extract_folder_id(folder_input: str) -> str:
    """
    Extract a Google Drive folder ID from a URL or return the raw ID.

    Accepts:
      - https://drive.google.com/drive/folders/FOLDER_ID
      - https://drive.google.com/drive/u/0/folders/FOLDER_ID
      - FOLDER_ID (raw)
    """
    match = DRIVE_FOLDER_URL_PATTERN.search(folder_input)
    if match:
        return match.group(1)
    return folder_input.strip()


def list_folder_contents(service, folder_id: str) -> list[dict]:
    """List all items in a Google Drive folder, handling pagination."""
    items = []
    page_token = None

    while True:
        response = (
            service.files()
            .list(
                q=f"'{folder_id}' in parents and trashed = false",
                spaces="drive",
                fields="nextPageToken, files(id, name, mimeType)",
                pageToken=page_token,
                pageSize=1000,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )

        items.extend(response.get("files", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return items


def find_msg_files_recursive(
    service, folder_id: str, path_prefix: str = ""
) -> list[dict]:
    """
    Recursively traverse a Google Drive folder and find all .msg files.

    Returns a list of dicts with keys: id, name, parent_id, path.
    """
    results = []
    items = list_folder_contents(service, folder_id)

    for item in items:
        item_path = f"{path_prefix}/{item['name']}" if path_prefix else item["name"]

        if item["mimeType"] == FOLDER_MIME_TYPE:
            sub_results = find_msg_files_recursive(
                service, item["id"], path_prefix=item_path
            )
            results.extend(sub_results)
        elif item["name"].lower().endswith(".msg"):
            results.append(
                {
                    "id": item["id"],
                    "name": item["name"],
                    "parent_id": folder_id,
                    "path": item_path,
                }
            )

    return results


def download_file(service, file_id: str, dest_path: Path) -> Path:
    """Download a file from Google Drive to a local path."""
    request = service.files().get_media(fileId=file_id)
    with open(dest_path, "wb") as f:
        downloader = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return dest_path


def create_drive_folder(service, folder_name: str, parent_id: str) -> str:
    """Create a folder in Google Drive and return its ID."""
    metadata = {
        "name": folder_name,
        "mimeType": FOLDER_MIME_TYPE,
        "parents": [parent_id],
    }
    folder = (
        service.files()
        .create(body=metadata, fields="id", supportsAllDrives=True)
        .execute()
    )
    return folder["id"]


def upload_folder_to_drive(
    service, local_folder: Path, parent_id: str
) -> str:
    """
    Recursively upload a local folder to Google Drive.

    Creates a Drive folder matching the local folder name, uploads all
    files, and recurses into subdirectories. Returns the created folder ID.
    """
    drive_folder_id = create_drive_folder(service, local_folder.name, parent_id)

    for item in sorted(local_folder.iterdir()):
        if item.is_dir():
            upload_folder_to_drive(service, item, drive_folder_id)
        elif item.is_file():
            mime_type, _ = mimetypes.guess_type(str(item))
            if mime_type is None:
                mime_type = "application/octet-stream"

            media = MediaFileUpload(str(item), mimetype=mime_type, resumable=True)
            service.files().create(
                body={
                    "name": item.name,
                    "parents": [drive_folder_id],
                },
                media_body=media,
                fields="id",
                supportsAllDrives=True,
            ).execute()

    return drive_folder_id


def process_msg_file(
    service, msg_info: dict, save_kwargs: dict, dry_run: bool = False
) -> bool:
    """
    Full pipeline for one .msg file: download, convert, upload.

    Returns True on success, False on failure.
    """
    if dry_run:
        print(f"  [DRY RUN] Would process: {msg_info['path']}")
        return True

    tmp_dir = None
    try:
        tmp_dir = tempfile.mkdtemp(prefix="gdrive_convert_")
        tmp_path = Path(tmp_dir)

        # Download .msg from Drive
        local_msg = tmp_path / msg_info["name"]
        download_file(service, msg_info["id"], local_msg)

        # Convert locally
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        convert_single_msg(local_msg, output_dir, save_kwargs)

        # Upload converted folder back to Drive
        converted_folder = output_dir / local_msg.stem
        if converted_folder.is_dir():
            upload_folder_to_drive(service, converted_folder, msg_info["parent_id"])
        else:
            print(f"  Warning: Expected output folder not found at {converted_folder}")
            return False

        return True
    except Exception as e:
        print(f"  Error: {e}")
        return False
    finally:
        if tmp_dir and Path(tmp_dir).exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)


def run(
    folder_input: str,
    credentials_path: str,
    token_path: str,
    save_kwargs: dict,
    dry_run: bool = False,
) -> None:
    """Main orchestration: authenticate, discover, convert, upload."""
    print("Authenticating with Google Drive...")
    creds = authenticate(credentials_path, token_path)
    service = build("drive", "v3", credentials=creds)
    print("Authenticated.\n")

    folder_id = extract_folder_id(folder_input)
    print(f"Scanning folder {folder_id} for .msg files...\n")

    msg_files = find_msg_files_recursive(service, folder_id)

    if not msg_files:
        print("No .msg files found in the specified folder.")
        return

    print(f"Found {len(msg_files)} .msg file(s):\n")
    for info in msg_files:
        print(f"  {info['path']}")
    print()

    if dry_run:
        print("[DRY RUN] No files will be converted or uploaded.")
        return

    errors = []

    for i, msg_info in enumerate(msg_files, 1):
        print(f"[{i}/{len(msg_files)}] {msg_info['path']}")
        success = process_msg_file(service, msg_info, save_kwargs, dry_run)
        if success:
            print("  Done\n")
        else:
            errors.append(msg_info["path"])
            print()

    # Summary
    succeeded = len(msg_files) - len(errors)
    print("=" * 60)
    print(f"Complete: {succeeded}/{len(msg_files)} converted and uploaded.")
    if errors:
        print("\nFailed files:")
        for path in errors:
            print(f"  - {path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert .msg files from Google Drive using extract-msg."
    )
    parser.add_argument(
        "folder",
        help="Google Drive folder URL or folder ID.",
    )
    parser.add_argument(
        "--credentials",
        default="credentials.json",
        help="Path to OAuth2 credentials JSON (default: credentials.json).",
    )
    parser.add_argument(
        "--token",
        default="token.json",
        help="Path to store/load auth token (default: token.json).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List .msg files without converting or uploading.",
    )
    add_conversion_args(parser)

    args = parser.parse_args()
    save_kwargs = build_save_kwargs(**kwargs_from_args(args))

    run(
        folder_input=args.folder,
        credentials_path=args.credentials,
        token_path=args.token,
        save_kwargs=save_kwargs,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
