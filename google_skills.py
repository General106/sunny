"""Gmail + Google Calendar skills. Connect once with: .venv/bin/python connect_google.py"""
import base64
import datetime
from email.mime.text import MIMEText

import config

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly",
          "https://www.googleapis.com/auth/gmail.send",
          "https://www.googleapis.com/auth/calendar.events"]
CLIENT_FILE = config.DATA / "google_credentials.json"
TOKEN_FILE = config.DATA / "google_token.json"
NOT_CONNECTED = "Your Google account isn't connected yet. Run connect_google.py once to link it."


def _service(api, version):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    if not TOKEN_FILE.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_FILE.write_text(creds.to_json())
    return build(api, version, credentials=creds, cache_discovery=False)


def check_email(max_results: int = 5):
    """Read out the newest unread emails in the inbox (sender and subject)."""
    gm = _service("gmail", "v1")
    if gm is None:
        return NOT_CONNECTED
    ids = gm.users().messages().list(userId="me", q="is:unread in:inbox",
                                     maxResults=int(max_results)).execute().get("messages", [])
    if not ids:
        return "No unread emails."
    lines = []
    for m in ids:
        msg = gm.users().messages().get(userId="me", id=m["id"], format="metadata",
                                        metadataHeaders=["From", "Subject"]).execute()
        h = {x["name"]: x["value"] for x in msg["payload"]["headers"]}
        sender = h.get("From", "someone").split("<")[0].strip(' "')
        lines.append(f"From {sender}: {h.get('Subject', '(no subject)')}")
    return "\n".join(lines)


def send_email(to: str, subject: str, body: str):
    """Send an email from the user's Gmail. The user confirms out loud first."""
    gm = _service("gmail", "v1")
    if gm is None:
        return NOT_CONNECTED
    msg = MIMEText(body)
    msg["to"], msg["subject"] = to, subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    gm.users().messages().send(userId="me", body={"raw": raw}).execute()
    return f"Email sent to {to}."


def calendar_today():
    """List today's events from Google Calendar."""
    cal = _service("calendar", "v3")
    if cal is None:
        return NOT_CONNECTED
    now = datetime.datetime.now().astimezone()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    events = cal.events().list(calendarId="primary", timeMin=start.isoformat(),
                               timeMax=(start + datetime.timedelta(days=1)).isoformat(),
                               singleEvents=True, orderBy="startTime").execute().get("items", [])
    if not events:
        return "Nothing on the calendar today."
    out = []
    for e in events:
        s = e["start"].get("dateTime")
        when = datetime.datetime.fromisoformat(s).strftime("%I:%M %p") if s else "all day"
        out.append(f"{when}: {e.get('summary', 'busy')}")
    return "\n".join(out)


def add_calendar_event(title: str, start: str, duration_minutes: int = 60):
    """Add an event to Google Calendar. `start` is ISO format like 2026-10-05T15:00. Confirmed first."""
    cal = _service("calendar", "v3")
    if cal is None:
        return NOT_CONNECTED
    begin = datetime.datetime.fromisoformat(start)
    if begin.tzinfo is None:
        begin = begin.astimezone()
    end = begin + datetime.timedelta(minutes=int(duration_minutes))
    cal.events().insert(calendarId="primary", body={
        "summary": title, "start": {"dateTime": begin.isoformat()},
        "end": {"dateTime": end.isoformat()}}).execute()
    return f"Added {title} on {begin.strftime('%A at %I:%M %p')}."


TOOLS = [check_email, send_email, calendar_today, add_calendar_event]
NEEDS_CONFIRMATION = {"send_email", "add_calendar_event"}
