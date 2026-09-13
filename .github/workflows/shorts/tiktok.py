"""TikTok cross-posting via the official Content Posting API.

One-time setup (see README or chat instructions):
  1. developer.tiktok.com app with Login Kit + Content Posting API
     (scopes: video.upload + video.publish), redirect URI:
     https://kikiwin25.github.io/kikiwin25/callback.html
  2. Repo secrets: TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET
  3. Run the "TikTok connect" workflow with the authorization code shown on
     the callback page. Tokens are stored in the private Actions cache
     (.tiktok_token file, restored/saved via actions/cache) — NEVER in the
     public repo.

Behaviour: direct-posts the finished video. Unaudited apps are forced to
SELF_ONLY (private) by TikTok — open the TikTok app and hit "Publish".
Once the app passes TikTok's audit, set cfg upload.privacy accordingly and
posts become public automatically.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .config import env

TOKEN_FILE = Path(".tiktok_token")
REDIRECT_URI = "https://kikiwin25.github.io/kikiwin25/callback.html"
API = "https://open.tiktokapis.com/v2"
SCOPES = "user.info.basic,video.upload,video.publish"

# Unaudited clients can only post SELF_ONLY; flip this after the audit.
PRIVACY_LEVEL = "SELF_ONLY"


class TikTokError(RuntimeError):
    pass


def _post_form(url: str, fields: dict) -> dict:
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _authorized_json(url: str, token: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.loads(r.read().decode())
    if body.get("error", {}).get("code") not in (None, "ok"):
        raise TikTokError(str(body.get("error")))
    return body.get("data") or {}


def authorize_url(client_key: str) -> str:
    q = urllib.parse.urlencode({
        "client_key": client_key,
        "scope": SCOPES,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "state": "hikayat-madhi",
    })
    return f"https://www.tiktok.com/v2/auth/authorize/?{q}"


def exchange_code(code: str) -> dict:
    """Authorization code -> token dict (called once by the connect workflow)."""
    ck = env("TIKTOK_CLIENT_KEY", True, "TikTok app Client Key")
    cs = env("TIKTOK_CLIENT_SECRET", True, "TikTok app Client Secret")
    tok = _post_form(f"{API}/oauth/token/", {
        "client_key": ck, "client_secret": cs,
        "grant_type": "authorization_code",
        "code": code.strip(),
        "redirect_uri": REDIRECT_URI,
    })
    if "access_token" not in tok:
        raise TikTokError(f"token exchange failed: {str(tok)[:200]}")
    tok["expires_at"] = time.time() + int(tok.get("expires_in", 86400)) - 600
    tok["refresh_expires_at"] = time.time() + int(tok.get("refresh_expires_in", 31536000)) - 86400
    TOKEN_FILE.write_text(json.dumps(tok))
    return tok


def _load_tokens() -> dict | None:
    try:
        return json.loads(TOKEN_FILE.read_text())
    except Exception:  # noqa: BLE001 — absent or corrupt = not connected
        return None


def get_access_token() -> str:
    tok = _load_tokens()
    if not tok:
        raise TikTokError("not connected — run the 'TikTok connect' workflow first")
    if time.time() < tok.get("expires_at", 0):
        return tok["access_token"]
    ck = env("TIKTOK_CLIENT_KEY", True)
    cs = env("TIKTOK_CLIENT_SECRET", True)
    tok = _post_form(f"{API}/oauth/token/", {
        "client_key": ck, "client_secret": cs,
        "grant_type": "refresh_token",
        "refresh_token": tok["refresh_token"],
    })
    if "access_token" not in tok:
        raise TikTokError(f"refresh failed: {str(tok)[:200]}")
    tok["expires_at"] = time.time() + int(tok.get("expires_in", 86400)) - 600
    tok["refresh_expires_at"] = time.time() + int(tok.get("refresh_expires_in", 31536000)) - 86400
    TOKEN_FILE.write_text(json.dumps(tok))
    return tok["access_token"]


def post_video(mp4: Path, title: str) -> str:
    """Direct-post one finished video. Returns the publish_id."""
    token = get_access_token()
    size = mp4.stat().st_size
    data = _authorized_json(
        f"{API}/post/publish/video/init/", token,
        {
            "post_info": {"title": title[:2200], "privacy_level": PRIVACY_LEVEL},
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": size,
                "chunk_size": size,
                "total_chunk_count": 1,
            },
        })
    upload_url = data.get("upload_url")
    publish_id = data.get("publish_id")
    if not upload_url or not publish_id:
        raise TikTokError(f"init returned no upload_url/publish_id: {str(data)[:200]}")

    req = urllib.request.Request(upload_url, data=mp4.read_bytes(),
                                 headers={"Content-Type": "video/mp4", "Content-Range": f"bytes 0-{size - 1}/{size}"})
    with urllib.request.urlopen(req, timeout=300) as r:
        r.read()

    try:
        status = _authorized_json(f"{API}/post/publish/status/fetch/", token,
                                  {"publish_id": publish_id})
        print(f"    [tiktok] status: {json.dumps(status.get('status', '?'))[:120]}")
    except Exception as exc:  # noqa: BLE001 — status is informational only
        print(f"    [tiktok] status check skipped ({str(exc)[:80]})")
    return publish_id


def main_cli(mp4: Path) -> int:
    """CLI used by the workflow: upload the final.mp4 of the last run."""
    if not mp4.exists():
        print(f"[tiktok] video not found: {mp4} — skipped")
        return 0
    try:
        get_access_token()
    except Exception as exc:  # noqa: BLE001 — not connected yet: silent skip
        print(f"[tiktok] not connected ({str(exc)[:90]}) — skipped")
        return 0

    title = "حكاية مضى — قصة جديدة كل يوم"
    script_json = mp4.parent / "script.json"
    if script_json.exists():
        try:
            meta = json.loads(script_json.read_text(encoding="utf-8"))
            title = str(meta.get("title") or meta.get("topic") or title)
        except Exception:  # noqa: BLE001
            pass

    print(f"[tiktok] uploading {mp4.name} ({mp4.stat().st_size // 1024} KB) as {PRIVACY_LEVEL} ...")
    pid = post_video(mp4, title)
    print(f"[tiktok] ✅ deposited (publish_id={pid}). Open the TikTok app → your profile →")
    print("         the private video → tap 'Everyone' → Publish. (Auto-public after audit.)")
    return 0


if __name__ == "__main__":
    video = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(sorted(Path("outputs").glob("*/final.mp4"))[-1])
    raise SystemExit(main_cli(video))
