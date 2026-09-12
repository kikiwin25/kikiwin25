"""ONE-TIME YouTube authorization — run this on YOUR computer:

    pip install -r requirements.txt
    python -m shorts.auth_setup

It opens your browser, you approve access, and it prints the three values to
store as GitHub Secrets (YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN).
Requires: an OAuth "Desktop app" client ID from Google Cloud Console
(README — Setup, step 2).
"""
from __future__ import annotations

import os

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


def main() -> None:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        raise SystemExit("Run:  pip install -r requirements.txt   first.")

    client_id = os.environ.get("YT_CLIENT_ID", "").strip() or input("Paste YT_CLIENT_ID: ").strip()
    client_secret = os.environ.get("YT_CLIENT_SECRET", "").strip() or input("Paste YT_CLIENT_SECRET: ").strip()
    if not client_id or not client_secret:
        raise SystemExit("Both client ID and secret are required.")

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")

    if not creds.refresh_token:
        raise SystemExit(
            "No refresh token returned. Revoke the app at "
            "https://myaccount.google.com/permissions then run this again."
        )

    print("\n" + "=" * 64)
    print("SUCCESS. Add these THREE values as GitHub repo secrets")
    print("(repo -> Settings -> Secrets and variables -> Actions -> New):")
    print("=" * 64)
    print(f"\n  YT_CLIENT_ID      = {client_id}")
    print(f"  YT_CLIENT_SECRET  = {client_secret}")
    print(f"  YT_REFRESH_TOKEN  = {creds.refresh_token}\n")
    print("=" * 64)
    print("Or from a terminal with the GitHub CLI:")
    print(f'  gh secret set YT_CLIENT_ID     --body "{client_id}"')
    print(f'  gh secret set YT_CLIENT_SECRET --body "{client_secret}"')
    print(f'  gh secret set YT_REFRESH_TOKEN --body "{creds.refresh_token}"')
    print('  gh secret set GEMINI_API_KEY   --body "your-gemini-api-key"')
    print("=" * 64)


if __name__ == "__main__":
    main()
