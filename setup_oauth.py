"""
One-time OAuth setup script.

Run this ONCE to authorize the bot to access your Google Drive:

    python setup_oauth.py

It will print a URL — open it in your browser, approve access,
then paste the full redirect URL (starting with http://localhost:8080/...)
back here. The token is saved to credentials/token.json.

IMPORTANT: Your OAuth client must be of type "Desktop app" in Google Cloud Console.
"""

from pathlib import Path
from urllib.parse import urlparse, parse_qs

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive"]
CLIENT_SECRETS = Path("credentials/oauth_client.json")
TOKEN_PATH = Path("credentials/token.json")
REDIRECT_PORT = 8080


def main() -> None:
    if not CLIENT_SECRETS.exists():
        print(f"ERROR: {CLIENT_SECRETS} not found.")
        print("Save your OAuth client JSON (Desktop app type) there and re-run.")
        return

    flow = InstalledAppFlow.from_client_secrets_file(
        str(CLIENT_SECRETS),
        scopes=SCOPES,
    )

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        redirect_uri=f"http://localhost:{REDIRECT_PORT}/",
    )

    print("\n" + "=" * 60)
    print("1. Open this URL in your browser and approve access:")
    print("=" * 60)
    print(f"\n{auth_url}\n")
    print("=" * 60)
    print(f"2. After approving, your browser will try to open:")
    print(f"   http://localhost:{REDIRECT_PORT}/?code=...")
    print("   (The page will fail to load — that's OK!)")
    print("3. Copy the FULL URL from your browser's address bar")
    print("   and paste it below.\n")

    redirect_response = input("Paste the full redirect URL here: ").strip()

    parsed = urlparse(redirect_response)
    params = parse_qs(parsed.query)

    if "code" not in params:
        print("\nERROR: No authorization code found in the URL.")
        print("Make sure you copied the full URL including ?code=...")
        return

    code = params["code"][0]

    flow.fetch_token(
        code=code,
        redirect_uri=f"http://localhost:{REDIRECT_PORT}/",
    )

    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(flow.credentials.to_json())
    print(f"\nToken saved to {TOKEN_PATH}")
    print("You can now start the bot.")


if __name__ == "__main__":
    main()
