"""Step 6 — upload to YouTube with the Data API (refresh-token OAuth)."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import env

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",  # playlist insert
]


def _credentials():
    from google.oauth2 import credentials as oauth_credentials

    return oauth_credentials.Credentials(
        token=None,
        refresh_token=env("YT_REFRESH_TOKEN", True, "Run `python -m shorts.auth_setup` on your computer once."),
        client_id=env("YT_CLIENT_ID", True, "Google Cloud OAuth Client ID (Desktop app)."),
        client_secret=env("YT_CLIENT_SECRET", True, "Google Cloud OAuth Client secret."),
        token_uri="https://oauth2.googleapis.com/token",
    )


def _youtube_api(cfg: dict):
    from googleapiclient.discovery import build

    return build("youtube", "v3", credentials=_credentials(), cache_discovery=False)


def _status_body(cfg: dict) -> dict:
    privacy = cfg["upload"].get("privacy", "public").lower()
    status: dict = {"selfDeclaredMadeForKids": bool(cfg["upload"].get("made_for_kids", False))}

    if privacy == "schedule":
        minutes = max(15, int(cfg["upload"].get("schedule_offset_minutes", 15)))
        publish_at = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        status["privacyStatus"] = "private"
        status["publishAt"] = publish_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    elif privacy in ("public", "unlisted", "private"):
        status["privacyStatus"] = privacy
    else:
        status["privacyStatus"] = "public"
    return status


def upload_short(final_path: Path, script, cfg: dict) -> dict:
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    youtube = _youtube_api(cfg)
    status = _status_body(cfg)

    body = {
        "snippet": {
            "title": script.title,
            "description": script.description,
            "tags": script.tags,
            "categoryId": str(cfg["upload"].get("category_id", "24")),
        },
        "status": status,
    }
    media = MediaFileUpload(str(final_path), mimetype="video/mp4",
                            chunksize=8 * 1024 * 1024, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    print(f"  [upload] {final_path.name} -> YouTube ({status['privacyStatus']})")
    response = None
    attempts = 0
    while response is None:
        try:
            _, response = request.next_chunk()
        except HttpError as exc:
            if exc.resp.status in (500, 502, 503, 504) and attempts < 5:
                attempts += 1
                wait = 5 * attempts
                print(f"    [retry] upload hiccup {exc.resp.status}, waiting {wait}s ...")
                time.sleep(wait)
                continue
            raise
    video_id = response["id"]
    url = f"https://www.youtube.com/watch?v={video_id}"
    print(f"  [upload] OK -> {url}")

    playlist_id = (cfg["upload"].get("playlist_id") or "").strip()
    if playlist_id:
        try:
            youtube.playlistItems().insert(
                part="snippet",
                body={"snippet": {"playlistId": playlist_id,
                                  "resourceId": {"kind": "youtube#video", "videoId": video_id}}},
            ).execute()
            print(f"  [upload] added to playlist {playlist_id}")
        except Exception as exc:  # noqa: BLE001 — playlist is best-effort
            print(f"  [warn] could not add to playlist: {str(exc)[:140]}")

    return {"id": video_id, "url": url, "privacy": status["privacyStatus"],
            "publishAt": status.get("publishAt", "")}
