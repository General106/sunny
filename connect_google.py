"""Link Jarvis to your Google account (Gmail + Calendar). Run once.

Needs data/google_credentials.json - an OAuth "Desktop app" client from
https://console.cloud.google.com (see README.md for the 5-minute steps).
A browser window opens; you sign in and approve the permissions yourself.
"""
from google_auth_oauthlib.flow import InstalledAppFlow

from google_skills import CLIENT_FILE, SCOPES, TOKEN_FILE

if not CLIENT_FILE.exists():
    raise SystemExit(f"Put your OAuth client file at {CLIENT_FILE} first (see README.md).")
creds = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), SCOPES).run_local_server(port=0)
TOKEN_FILE.write_text(creds.to_json())
TOKEN_FILE.chmod(0o600)
print("Google connected. Try: 'Hey Jarvis, what's on my calendar today?'")
