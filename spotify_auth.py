"""Spotify account login (Premium) so Sunny can start songs through the Web API.

Playing a single track via the Web API makes it its own one-song queue, so Spotify's own
Autoplay picks what comes next straight away (instead of the rest of the album).
Connect once with:  .venv/bin/python connect_spotify.py
"""
import base64
import json
import time
import urllib.parse
import urllib.request

import config

TOKEN_FILE = config.DATA / "spotify_token.json"
REDIRECT = "http://127.0.0.1:8888/callback"
SCOPES = "user-modify-playback-state user-read-playback-state"


def _client():
    c = json.loads((config.DATA / "spotify.json").read_text())
    return c["client_id"], c["client_secret"]


def _token_request(data):
    cid, secret = _client()
    auth = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    req = urllib.request.Request("https://accounts.spotify.com/api/token",
                                 data=urllib.parse.urlencode(data).encode(),
                                 headers={"Authorization": f"Basic {auth}"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def save(tok):
    tok["expires_at"] = time.time() + tok.get("expires_in", 3600) - 60
    old = json.loads(TOKEN_FILE.read_text()) if TOKEN_FILE.exists() else {}
    tok.setdefault("refresh_token", old.get("refresh_token"))
    TOKEN_FILE.write_text(json.dumps(tok))
    TOKEN_FILE.chmod(0o600)


def access_token():
    """A valid user access token, or None if the account isn't connected."""
    if not TOKEN_FILE.exists():
        return None
    tok = json.loads(TOKEN_FILE.read_text())
    if time.time() > tok.get("expires_at", 0):
        tok = _token_request({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
        save(tok)
        tok = json.loads(TOKEN_FILE.read_text())
    return tok["access_token"]


def _api(method, path, body=None):
    token = access_token()
    req = urllib.request.Request("https://api.spotify.com/v1" + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


def play(uri):
    """Start `uri` on this laptop's Spotify. Returns True on success, False to fall back."""
    if not access_token():
        return False
    import socket
    devices = _api("GET", "/me/player/devices").get("devices", [])
    host = socket.gethostname().lower()
    local = [d for d in devices if d.get("type") == "Computer"]
    device = next((d for d in local if host in d.get("name", "").lower()), None) or \
        next((d for d in local if d.get("is_active")), None) or (local[0] if local else None)
    if not device:
        return False
    q = "?device_id=" + urllib.parse.quote(device["id"])
    body = {"uris": [uri]} if ":track:" in uri else {"context_uri": uri}
    _api("PUT", "/me/player/play" + q, body)
    return True
