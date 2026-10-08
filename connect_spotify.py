"""Link your Spotify Premium account to Sunny. Run once; approve in the browser that opens."""
import http.server
import secrets
import urllib.parse
import webbrowser

import spotify_auth

state = secrets.token_urlsafe(16)
cid, _ = spotify_auth._client()
url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode({
    "client_id": cid, "response_type": "code", "redirect_uri": spotify_auth.REDIRECT,
    "scope": spotify_auth.SCOPES, "state": state})
result = {}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if q.get("state", [""])[0] == state and "code" in q:
            result["code"] = q["code"][0]
            msg = "Spotify connected to Sunny. You can close this tab."
        else:
            msg = "Spotify login was cancelled or failed. You can close this tab."
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(msg.encode())

    def log_message(self, *args):
        pass


print("Opening Spotify in your browser - log in and click Agree/Allow.\n" + url)
webbrowser.open(url)
server = http.server.HTTPServer(("127.0.0.1", 8888), Handler)
while "code" not in result:
    server.handle_request()
    if "code" not in result:
        raise SystemExit("Login cancelled.")
spotify_auth.save(spotify_auth._token_request({
    "grant_type": "authorization_code", "code": result["code"],
    "redirect_uri": spotify_auth.REDIRECT}))
print("Spotify connected. Try: 'Hey Sunny, play Loser by Tame Impala'.")
