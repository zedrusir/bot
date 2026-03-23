"""
One-time OAuth setup script.

Run this ONCE to authorize the bot to access your Google Drive:

    python setup_oauth.py

It will print a URL — open it in your browser, approve access,
paste the code back here. The token is saved to credentials/token.json.
"""

import json
from pathlib import Path

from google_auth_oauthlib.flow import Flow

SCOPES = ["https://www.googleapis.com/auth/drive"]
CLIENT_SECRETS = Path("credentials/oauth_client.json")
TOKEN_PATH = Path("credentials/token.json")


def main() -> None:
    if not CLIENT_SECRETS.exists():
        print(f"ERROR: {CLIENT_SECRETS} not found.")
        print("Save your OAuth client JSON there and re-run.")
        return

    flow = Flow.from_client_secrets_file(
        str(CLIENT_SECRETS),
        scopes=SCOPES,
        redirect_uri="urn:ietf:wg:oauth:2.0:oob",
    )

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
    )

    print("\n" + "=" * 60)
    print("Open this URL in your browser and approve access:")
    print("=" * 60)
    print(f"\n{auth_url}\n")
    print("=" * 60)

    code = input("Paste the authorization code here: ").strip()
    flow.fetch_token(code=code)

    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(flow.credentials.to_json())
    print(f"\n✅ Token saved to {TOKEN_PATH}")
    print("You can now start the bot.")


if __name__ == "__main__":
    main()
