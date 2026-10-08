"""Things Jarvis can do on this computer. Each function is exposed to the LLM as a tool."""
import datetime
import json
import shutil
import subprocess
import urllib.parse

import config


def _run(args):
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def get_time():
    """Current local date and time."""
    return datetime.datetime.now().strftime("%A %d %B %Y, %I:%M %p")


def open_app(name: str):
    """Launch a desktop application by name, e.g. 'firefox', 'files', 'terminal', 'vscode'."""
    aliases = {"files": "nautilus", "file manager": "nautilus", "terminal": "gnome-terminal",
               "browser": "xdg-open https://", "vscode": "code", "vs code": "code",
               "calculator": "gnome-calculator", "settings": "gnome-control-center",
               "chrome": "google-chrome", "text editor": "gnome-text-editor"}
    import re
    name = re.sub(r"\s+(on|in) (mine|my (pc|laptop|computer)|this (pc|laptop|computer))$", "", name.lower().strip())
    # Whisper often hears "Claude" as "cloud".
    name = {"cloud": "claude", "cloud ai": "claude", "claude ai": "claude", "cloud app": "claude",
            "clod": "claude", "claud": "claude"}.get(name, name)
    cmd = aliases.get(name, name)
    exe = cmd.split()[0]
    if shutil.which(exe):
        _run(cmd.split())
        return f"Opened {name}."
    desktop = _find_desktop_entry(name)
    if desktop:
        _run(["gtk-launch", desktop])
        return f"Opened {name}."
    web = SITES.get(name) or {"spotify": "https://open.spotify.com"}.get(name)
    if web:
        _run(["xdg-open", web])
        return f"{name} isn't installed as an app, so I opened the web version instead."
    m = re.search(r"\s(?:on|in)\s+(?:the\s+)?([a-z0-9 ]+)$", name)
    if m and m.group(1) != name:     # "voice command on claude" -> open Claude itself
        res = open_app(m.group(1))
        if not res.startswith("FAILED"):
            return f"{res} I can't press buttons inside it though - {name.split(' on ')[0].split(' in ')[0]} is up to you."
    return f"FAILED: {name} is not installed on this computer. Nothing was opened."


def switch_to_tab(name: str, browser: str = ""):
    """Switch to the open browser tab whose title contains `name` (e.g. 'whatsapp', 'youtube').
    Opens the site instead if no such tab is open."""
    import re
    import time
    want = re.sub(r"[^a-z0-9]", "", name.lower().replace(" web", ""))
    classes = ([BROWSERS.get(browser.lower(), browser.lower())] if browser else
               ["google-chrome", "firefox", "chromium"])
    for cls in classes:
        wins = subprocess.run(["xdotool", "search", "--onlyvisible", "--class", cls],
                              capture_output=True, text=True).stdout.split()
        for win in reversed(wins):
            subprocess.run(["xdotool", "windowactivate", "--sync", win])
            first = None
            for _ in range(40):   # walk through the tabs until the title matches
                title = subprocess.run(["xdotool", "getwindowname", win], capture_output=True,
                                       text=True).stdout.strip()
                if want and want in re.sub(r"[^a-z0-9]", "", title.lower()):
                    return f"Switched to the {name} tab."
                if title == first:
                    break          # went all the way round
                first = first or title
                subprocess.run(["xdotool", "key", "--clearmodifiers", "ctrl+Tab"])
                time.sleep(0.15)
    if SITES.get(name.lower().strip()):
        _browse(SITES[name.lower().strip()], browser)
        return f"There was no {name} tab, so I opened {name}."
    return f"FAILED: there's no {name} tab open."


def video_control(action: str):
    """Play/pause a video playing in the browser (YouTube etc.): play, pause, toggle."""
    out = subprocess.run(["gdbus", "call", "--session", "--dest", "org.freedesktop.DBus",
                          "--object-path", "/org/freedesktop/DBus", "--method",
                          "org.freedesktop.DBus.ListNames"], capture_output=True, text=True).stdout
    import re
    players = re.findall(r"'(org\.mpris\.MediaPlayer2\.(?:chrom\w*|firefox)[\w.]*)'", out)
    method = {"play": "Play", "resume": "Play", "pause": "Pause", "stop": "Pause"}.get(action, "PlayPause")
    for p in players:
        r = subprocess.run(["gdbus", "call", "--session", "--dest", p, "--object-path",
                            "/org/mpris/MediaPlayer2", "--method",
                            f"org.mpris.MediaPlayer2.Player.{method}"], capture_output=True)
        if r.returncode == 0:
            return f"Video {'paused' if method == 'Pause' else 'playing'}."
    # Fallback: press YouTube's own play/pause key ("k") in the browser window.
    for cls in ("google-chrome", "firefox", "chromium"):
        wins = subprocess.run(["xdotool", "search", "--onlyvisible", "--class", cls],
                              capture_output=True, text=True).stdout.split()
        if wins:
            title = subprocess.run(["xdotool", "getwindowname", wins[-1]], capture_output=True,
                                   text=True).stdout
            if "YouTube" not in title:
                return "FAILED: the browser isn't showing a YouTube video right now."
            subprocess.run(["xdotool", "windowactivate", "--sync", wins[-1], "key", "--clearmodifiers", "k"])
            return "Toggled the YouTube video."
    return "FAILED: I can't find a video playing in the browser."


def browser_tab(action: str, browser: str = ""):
    """Control browser tabs in Chrome/Firefox: close (current tab), new, next, previous, reopen."""
    keys = {"close": "ctrl+w", "new": "ctrl+t", "next": "ctrl+Tab", "previous": "ctrl+shift+Tab",
            "reopen": "ctrl+shift+t", "first": "ctrl+1", "last": "ctrl+9"}
    key = keys.get(action)
    if not key:
        return f"FAILED: unknown tab action {action}."
    classes = ([BROWSERS.get(browser.lower(), browser.lower())] if browser else
               ["google-chrome", "firefox", "chromium"])
    for cls in classes:
        wins = subprocess.run(["xdotool", "search", "--onlyvisible", "--class", cls],
                              capture_output=True, text=True).stdout.split()
        if wins:
            # Prefer the browser window that's active right now, else the most recent one.
            active = subprocess.run(["xdotool", "getactivewindow"], capture_output=True, text=True).stdout.strip()
            win = active if active in wins else wins[-1]
            title = subprocess.run(["xdotool", "getwindowname", win], capture_output=True,
                                   text=True).stdout.strip().rsplit(" - ", 1)[0]
            import re
            title = re.sub(r"^\(\d+\)\s*|\s*-\s*(YouTube|Google Search)$|[\u200b]", "", title).strip()
            subprocess.run(["xdotool", "windowactivate", "--sync", win, "key", "--clearmodifiers", key])
            return {"close": f"Closed the tab: {title[:60]}.", "new": "Opened a new tab.",
                    "next": "Next tab.", "previous": "Previous tab.",
                    "reopen": "Reopened the last closed tab.", "first": "First tab.",
                    "last": "Last tab."}[action]
    return "FAILED: no browser window is open."


def _window_ids(name, visible=True):
    """Find an app's windows: by window class, by its launcher's program name, then by title."""
    import os
    key = {"chrome": "google-chrome", "terminal": "gnome-terminal", "files": "nautilus",
           "vs code": "code", "vscode": "code", "cloud": "claude"}.get(name.lower().strip(), name.lower().strip())
    flags = ["--onlyvisible"] if visible else []
    candidates = [key]
    desktop = _find_desktop_entry(key)
    if desktop:
        for d in ["/usr/share/applications", "/var/lib/snapd/desktop/applications",
                  os.path.expanduser("~/.local/share/applications")]:
            path = f"{d}/{desktop}.desktop"
            if os.path.exists(path):
                for line in open(path, errors="ignore"):
                    if line.startswith(("Exec=", "StartupWMClass=")):
                        candidates.append(os.path.basename(line.split("=", 1)[1].split()[0]))
                break
        candidates.append(desktop.split(".")[-1])   # org.gnome.eog -> eog
    for cls in dict.fromkeys(c for c in candidates if c):
        wins = subprocess.run(["xdotool", "search", *flags, "--class", cls],
                              capture_output=True, text=True).stdout.split()
        if wins:
            return wins
    return subprocess.run(["xdotool", "search", *flags, "--name", f"(?i){key}"],
                          capture_output=True, text=True).stdout.split()


def window_control(name: str, action: str):
    """Minimize, maximize, restore or switch to (focus) an app's window, e.g. ('spotify', 'minimize')."""
    wins = _window_ids(name) or (_window_ids(name, visible=False) if action in ("minimize", "maximize") else [])
    if not wins:
        return open_app(name) if action == "focus" else f"FAILED: {name} isn't open."
    win = wins[-1]
    if action == "minimize":
        for w in wins:
            subprocess.run(["xdotool", "windowminimize", w])
        return f"Minimized {name}."
    subprocess.run(["xdotool", "windowactivate", "--sync", win])
    if action == "maximize":
        subprocess.run(["xdotool", "key", "--clearmodifiers", "super+Up"])
        return f"Maximized {name}."
    if action == "restore":
        subprocess.run(["xdotool", "key", "--clearmodifiers", "super+Down"])
        return f"Restored {name}."
    return f"Switched to {name}."


def is_app_open(name: str):
    """Check whether an application (e.g. claude, chrome, spotify) currently has a window open."""
    return f"{name} is open." if _window_ids(name) else f"{name} is not open."


def _find_desktop_entry(name):
    """Find an installed app's .desktop id by its file name or display Name."""
    import glob
    import os
    name = name.lower().strip()
    dirs = ["/usr/share/applications", "/var/lib/snapd/desktop/applications",
            "/var/lib/flatpak/exports/share/applications",
            os.path.expanduser("~/.local/share/applications"),
            os.path.expanduser("~/.local/share/flatpak/exports/share/applications")]
    for d in dirs:
        for path in glob.glob(f"{d}/*.desktop"):
            file_id = os.path.basename(path)[:-8]
            if name in file_id.lower():
                return file_id
            try:
                with open(path, errors="ignore") as f:
                    for line in f:
                        if line.startswith("Name=") and name in line[5:].lower():
                            return file_id
            except OSError:
                pass
    return None


def close_app(name: str):
    """Close an open application window by name, e.g. 'spotify', 'chrome', 'firefox'."""
    key = name.lower().strip()
    key = {"chrome": "google-chrome", "terminal": "gnome-terminal", "files": "nautilus",
           "vs code": "code", "vscode": "code"}.get(key, key)
    if key == "spotify" and _spotify_running():
        ok, _ = _spotify("org.mpris.MediaPlayer2.Quit")
        if ok:
            return "Closed Spotify."
    found = _window_ids(name)
    if not found:
        return f"FAILED: {name} doesn't seem to be open."
    for wid in found:
        subprocess.run(["xdotool", "windowactivate", "--sync", wid, "key", "--clearmodifiers", "alt+F4"])
    return f"Closed {name}."


SITES = {"youtube": "https://www.youtube.com", "google": "https://www.google.com",
         "gmail": "https://mail.google.com", "whatsapp": "https://web.whatsapp.com",
         "instagram": "https://www.instagram.com", "facebook": "https://www.facebook.com",
         "netflix": "https://www.netflix.com", "chatgpt": "https://chatgpt.com",
         "claude": "https://claude.ai", "claude ai": "https://claude.ai", "cloud ai": "https://claude.ai",
         "github": "https://github.com",
         "linkedin": "https://www.linkedin.com", "twitter": "https://x.com", "x": "https://x.com",
         "amazon": "https://www.amazon.in", "maps": "https://maps.google.com",
         "google maps": "https://maps.google.com", "drive": "https://drive.google.com",
         "google drive": "https://drive.google.com", "calendar": "https://calendar.google.com",
         "spotify web": "https://open.spotify.com", "reddit": "https://www.reddit.com",
         "email": "https://mail.google.com", "mail": "https://mail.google.com",
         "my email": "https://mail.google.com", "inbox": "https://mail.google.com",
         "emails": "https://mail.google.com", "my calendar": "https://calendar.google.com"}
BROWSERS = {"chrome": "google-chrome", "google chrome": "google-chrome", "firefox": "firefox"}


def _browse(url, browser=""):
    exe = BROWSERS.get((browser or "").lower().strip())
    _run([exe, url] if exe and shutil.which(exe) else ["xdg-open", url])


def open_email(which: str = "first"):
    """Open the newest (first) unread email in Gmail in the browser."""
    try:
        import google_skills
        gm = google_skills._service("gmail", "v1")
    except Exception:
        gm = None
    if gm is None:
        _browse("https://mail.google.com")
        return "Opened Gmail. Google isn't connected, so I couldn't jump to a specific email."
    msgs = gm.users().messages().list(userId="me", q="is:unread in:inbox", maxResults=1).execute().get("messages", [])
    if not msgs:
        _browse("https://mail.google.com")
        return "No unread emails, so I just opened your inbox."
    _browse(f"https://mail.google.com/mail/u/0/#inbox/{msgs[0]['threadId']}")
    return "Opened your newest unread email."


def open_url(url: str, browser: str = ""):
    """Open a website (a URL or a site name like 'youtube'). Optional browser: chrome or firefox."""
    url = SITES.get(url.lower().strip(), url)
    if not url.startswith(("http://", "https://")):
        url = "https://" + url.replace(" ", "")
    _browse(url, browser)
    return f"Opened {url.split('//')[1].split('/')[0]}" + (f" in {browser}." if browser else ".")


def web_search(query: str, browser: str = ""):
    """Search Google for something and show the results in the browser."""
    _browse("https://www.google.com/search?q=" + urllib.parse.quote(query), browser)
    return f"Searching Google for {query}."


def play_youtube(query: str, browser: str = ""):
    """Play a video or song on YouTube (starts the top result). 'shorts' opens YouTube Shorts."""
    import re
    import urllib.request
    if re.fullmatch(r"(a |some |is |the |one )?(youtube )?(shorts?|shots?)( video)?s?", query.lower().strip()):
        _browse("https://www.youtube.com/shorts", browser)
        return "Opened YouTube Shorts."
    results = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(query)
    try:
        req = urllib.request.Request(results, headers={"User-Agent": "Mozilla/5.0",
                                                       "Accept-Language": "en"})
        with urllib.request.urlopen(req, timeout=8) as r:
            vid = re.search(r'"videoId":"([\w-]{11})"', r.read().decode("utf-8", "ignore"))
    except Exception:
        vid = None
    if vid:
        _browse(f"https://www.youtube.com/watch?v={vid.group(1)}", browser)
        return f"Playing {query} on YouTube."
    _browse(results, browser)
    return f"I opened YouTube results for {query}. Pick the one you want."


def type_text(text: str):
    """Type text into whatever window currently has focus."""
    if not shutil.which("xdotool"):
        return "xdotool isn't installed yet, so I can't type."
    subprocess.run(["xdotool", "type", "--delay", "20", text])
    return "Typed it."


def set_volume(percent: int):
    """Set the speaker volume, 0-100."""
    percent = max(0, min(100, int(percent)))
    subprocess.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{percent}%"])
    return f"Volume set to {percent} percent."


MPRIS = ["gdbus", "call", "--session", "--dest", "org.mpris.MediaPlayer2.spotify",
         "--object-path", "/org/mpris/MediaPlayer2"]


def _spotify(method, *args):
    out = subprocess.run(MPRIS + ["--method", method, *args], capture_output=True, text=True)
    return out.returncode == 0, out.stdout.strip()


def _spotify_running():
    ok, _ = _spotify("org.freedesktop.DBus.Properties.Get", "org.mpris.MediaPlayer2.Player",
                     "PlaybackStatus")
    return ok


def media_control(action: str):
    """Control music in Spotify (or any player): play, pause, next, previous."""
    action = action.lower().strip()
    methods = {"play": "Play", "resume": "Play", "pause": "Pause", "stop": "Pause",
               "next": "Next", "skip": "Next", "previous": "Previous", "back": "Previous",
               "toggle": "PlayPause"}
    method = methods.get(action)
    if not method:
        return f"FAILED: unknown media action {action}."
    if _spotify_running():
        ok, _ = _spotify(f"org.mpris.MediaPlayer2.Player.{method}")
        if ok:
            return f"Spotify: {action} done."
    keys = {"Play": "XF86AudioPlay", "Pause": "XF86AudioPlay", "PlayPause": "XF86AudioPlay",
            "Next": "XF86AudioNext", "Previous": "XF86AudioPrev"}
    subprocess.run(["xdotool", "key", keys[method]])
    return f"Sent the {action} key to the active player."


def now_playing():
    """What song is playing on Spotify right now."""
    if not _spotify_running():
        return "Spotify isn't open."
    ok, out = _spotify("org.freedesktop.DBus.Properties.Get", "org.mpris.MediaPlayer2.Player",
                       "Metadata")
    import re
    title = re.search(r"'xesam:title': <'(.*?)'>", out)
    artist = re.search(r"'xesam:artist': <\['(.*?)'", out)
    if not title or not title.group(1):
        return "Nothing is playing."
    return f"{title.group(1)} by {artist.group(1) if artist else 'unknown artist'}"


def _spotify_api_search(query):
    """Find the best track/playlist/album URI using Spotify's Web API (client credentials)."""
    import base64
    import urllib.request
    creds_file = config.DATA / "spotify.json"
    if not creds_file.exists():
        return None
    c = json.loads(creds_file.read_text())
    auth = base64.b64encode(f"{c['client_id']}:{c['client_secret']}".encode()).decode()
    req = urllib.request.Request("https://accounts.spotify.com/api/token",
                                 data=b"grant_type=client_credentials",
                                 headers={"Authorization": f"Basic {auth}"})
    with urllib.request.urlopen(req, timeout=8) as r:
        token = json.loads(r.read())["access_token"]
    q = query.lower()
    kind = "playlist" if "playlist" in q else "album" if "album" in q else "track"
    import re
    search = re.sub(r"\s+by\s+", " ", query)   # "X by Y" -> "X Y" reads better to Spotify search
    if kind != "track":                          # "some lo-fi playlist" -> "lo-fi"
        import re
        search = re.sub(r"\b(some|a|an|the|my|playlist|album|songs?)\b", " ", q).strip() or query
    url = ("https://api.spotify.com/v1/search?limit=10&type=" + kind + "&q="
           + urllib.parse.quote(search))
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=8) as r:
        items = [i for i in json.loads(r.read()).get(kind + "s", {}).get("items", []) if i]
    if not items:
        return None
    item = items[0]                              # Spotify's own top result
    import spotify_auth
    if (kind == "track" and not spotify_auth.TOKEN_FILE.exists()
            and item.get("album", {}).get("album_type") != "single"):
        # Same song released as a single? Then Spotify Autoplay takes over right after it,
        # instead of carrying on through the rest of an album.
        artist = item["artists"][0]["id"]
        for other in items[1:]:
            if (other["name"].lower() == item["name"].lower() and other["artists"]
                    and other["artists"][0]["id"] == artist
                    and other.get("album", {}).get("album_type") == "single"):
                item = other
                break
    who = ", ".join(a["name"] for a in item.get("artists", [])) or item.get("owner", {}).get("display_name", "")
    return item["uri"], f"{item['name']} by {who}" if who else item["name"]


def _ensure_spotify_open():
    import time
    if _spotify_running():
        return True
    open_app("spotify")
    for _ in range(30):
        time.sleep(0.5)
        if _spotify_running():
            time.sleep(2)
            return True
    return False


def spotify_play(query: str):
    """Play a specific song, artist, album or playlist on Spotify, e.g. 'Kesariya by Arijit Singh'."""
    found = None
    for attempt in range(2):
        try:
            found = _spotify_api_search(query)
            break
        except Exception as e:
            print(f"[spotify] API search failed: {e}")
            import time
            time.sleep(1.5)
    if not found:
        return spotify_search(query)
    uri, label = found
    if not _ensure_spotify_open():
        return "FAILED: Spotify didn't start."
    import spotify_auth
    try:   # Premium Web API: a one-song queue, so Spotify's Autoplay picks what's next
        ok = spotify_auth.play(uri)
    except Exception as e:
        print(f"[spotify] Web API play failed ({e}); using the desktop app link instead")
        ok = False
    if not ok:
        ok, _ = _spotify("org.mpris.MediaPlayer2.Player.OpenUri", uri)
    return f"Playing {label} on Spotify." if ok else "FAILED: couldn't talk to Spotify."


def spotify_search(query: str):
    """Search Spotify for a song, artist, album or playlist and show the results in Spotify."""
    _ensure_spotify_open()
    ok, _ = _spotify("org.mpris.MediaPlayer2.Player.OpenUri",
                     "spotify:search:" + urllib.parse.quote(query))
    if not ok:
        return "FAILED: couldn't talk to Spotify."
    return f"I couldn't start it automatically, so I opened Spotify's search for {query}. Pick the one you want."


def power_action(action: str):
    """Shut down, restart, sleep (suspend) or log out of the laptop. The user confirms first."""
    cmds = {"shutdown": "systemctl poweroff", "restart": "systemctl reboot",
            "sleep": "systemctl suspend", "logout": "gnome-session-quit --logout --no-prompt"}
    cmd = cmds.get(action)
    if not cmd:
        return f"FAILED: unknown power action {action}."
    # Small delay so the spoken reply finishes first.
    _run(["sh", "-c", f"sleep 4; {cmd}"])
    return {"shutdown": "Shutting down in a few seconds.", "restart": "Restarting in a few seconds.",
            "sleep": "Going to sleep in a few seconds.", "logout": "Logging you out."}[action]


KEEP_OPEN = {"mutter-x11-frames", "gjs", "gnome-shell", "com.anthropic.claude"}   # never closed


def close_all_apps():
    """Close every open app window gracefully (apps can still ask to save). Keeps Claude open."""
    import re
    import time
    out = subprocess.run(["xdotool", "search", "--onlyvisible", "--name", "."],
                         capture_output=True, text=True).stdout.split()
    closed, seen = [], set()
    for w in out:
        props = subprocess.run(["xprop", "-id", w, "WM_CLASS", "_NET_WM_WINDOW_TYPE"],
                               capture_output=True, text=True).stdout
        cls = re.search(r'WM_CLASS\(STRING\) = "([^"]+)"', props)
        if not cls or "_NET_WM_WINDOW_TYPE_NORMAL" not in props or cls.group(1).lower() in KEEP_OPEN:
            continue
        name = cls.group(1).lower()
        if name == "spotify" and _spotify_running():
            _spotify("org.mpris.MediaPlayer2.Quit")
        else:
            subprocess.run(["xdotool", "windowactivate", "--sync", w, "key", "--clearmodifiers", "alt+F4"])
            time.sleep(0.3)
        if name not in seen:
            seen.add(name)
            closed.append({"google-chrome": "Chrome", "eog": "image viewer",
                           "gnome-terminal-server": "terminal", "org.gnome.nautilus": "Files"}.get(name, name))
    if not closed:
        return "Nothing to close - only Claude is open."
    return f"Closed {', '.join(closed)}. Left Claude open. If something asked to save, check it."


def lock_screen():
    """Lock the computer screen."""
    _run(["loginctl", "lock-session"])
    return "Locking the screen."


def remember(fact: str):
    """Save a fact about the user to long-term memory, e.g. preferences, names, plans."""
    mem = load_memory()
    mem.append({"fact": fact, "saved": datetime.date.today().isoformat()})
    config.MEMORY_FILE.write_text(json.dumps(mem, indent=2))
    return "Got it, I'll remember that."


def load_memory():
    if config.MEMORY_FILE.exists():
        return json.loads(config.MEMORY_FILE.read_text())
    return []


SNAP_NAMES = {"vs code": "code", "vscode": "code", "visual studio code": "code",
              "chrome": "chromium", "obs": "obs-studio", "android studio": "android-studio",
              "pycharm": "pycharm-community", "intellij": "intellij-idea-community",
              "whatsapp": "whatsapp-linux-app", "teams": "teams-for-linux", "zoom": "zoom-client"}
PRETTY = {"code": "VS Code", "obs-studio": "OBS Studio", "chromium": "Chromium"}


def pretty_app(name):
    snap = SNAP_NAMES.get(name.lower().strip(), name.lower().strip())
    return PRETTY.get(snap, name)


def install_app(name: str):
    """Install an app from the Snap store (e.g. spotify, discord, vlc). Opens a terminal window
    where the user types their password. The user is asked to confirm first."""
    import re
    snap = SNAP_NAMES.get(name.lower().strip(), name.lower().strip()).replace(" ", "-")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", snap):
        return f"FAILED: '{name}' doesn't look like a valid app name."
    found = subprocess.run(["snap", "info", snap], capture_output=True, text=True)
    if found.returncode != 0:
        return f"FAILED: there's no app called {snap} in the Snap store."
    if subprocess.run(["snap", "list", snap], capture_output=True).returncode == 0:
        return f"{pretty_app(snap)} is already installed."
    classic = " --classic" if re.search(r"latest/stable:.*\bclassic\b", found.stdout) else ""
    _run(["gnome-terminal", "--title", f"Installing {snap}", "--", "bash", "-c",
          f"echo 'Jarvis: installing {snap}. Type your password below.'; "
          f"sudo snap install {snap}{classic} && echo && echo 'Done! You can close this window.'; read"])
    return (f"Opened a terminal window to install {snap}. The user needs to type their password "
            f"there; it isn't installed until that finishes.")


DANGEROUS = (r"\brm\s+-[a-z]*r[a-z]*f?\s+(/|~|\$HOME|\*)(\s|$)", r"\bmkfs", r"\bdd\s+.*of=/dev/",
             r":\(\)\s*\{", r">\s*/dev/sd", r"\bchmod\s+-R\s+777\s+/", r"\bshred\b",
             r"curl[^|]*\|\s*(sudo\s+)?(ba)?sh", r"wget[^|]*\|\s*(sudo\s+)?(ba)?sh")


def run_shell(command: str):
    """Run a shell command on the computer. The user is asked to confirm out loud first."""
    import re
    # Confirmation is enforced by the caller (jarvis.py) before this runs.
    if any(re.search(p, command) for p in DANGEROUS):
        return "FAILED: that command could wipe or break the system, so I won't run it."
    if "sudo" in command.split():
        return ("FAILED: I can't run sudo commands because they need your password. "
                "Ask the user to run it themselves in a terminal.")
    try:
        out = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=60)
        result = (out.stdout + out.stderr).strip()
        return result[-1500:] or f"Finished with exit code {out.returncode}."
    except subprocess.TimeoutExpired:
        return "That command took too long, so I stopped it."


FAST_MODE = {"on": False}


def keep_alive():
    """How long Ollama keeps the AI model in GPU memory after each reply."""
    import power
    if config.BATTERY_MODE and not power.on_ac():
        return "1m"          # on battery: free the GPU quickly so it can sleep
    return -1 if FAST_MODE["on"] else "5m"


def fast_mode(on: bool):
    """Keep the AI model loaded for instant replies (on), or free the GPU memory (off)."""
    import ollama
    import power
    if on and config.BATTERY_MODE and not power.on_ac():
        return "FAILED: you're on battery, so fast mode is off to save power. Plug in and ask again."
    FAST_MODE["on"] = bool(on)
    if on:
        ollama.generate(model=config.LLM_MODEL, prompt="", keep_alive=-1)   # load and pin it
        return "Fast mode on. I'll stay loaded - instant replies, but I'm hogging about 4 gigs of GPU."
    ollama.generate(model=config.LLM_MODEL, prompt="", keep_alive=0)        # unload right now
    return "Fast mode off. GPU memory freed - go game. First reply after a break will be slower."


def get_weather(city: str = ""):
    """Current weather and today's forecast. Leave city empty for the user's location."""
    import urllib.request
    url = "https://wttr.in/" + urllib.parse.quote(city or config.HOME_CITY) + "?format=%C,+%t,+feels+like+%f,+humidity+%h"
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            return r.read().decode().strip()
    except Exception:
        return "I couldn't reach the weather service."


def morning_briefing():
    """Give a morning briefing: time, weather, today's calendar and unread email."""
    parts = [f"Time: {get_time()}", f"Weather: {get_weather()}"]
    try:
        import google_skills
        parts += [f"Calendar:\n{google_skills.calendar_today()}",
                  f"Unread email:\n{google_skills.check_email(5)}"]
    except Exception as e:
        parts.append(f"(Google unavailable: {e})")
    return "\n".join(parts)


TOOLS = [get_time, get_weather, morning_briefing, fast_mode, power_action, close_all_apps, open_app, close_app, window_control, switch_to_tab, browser_tab, video_control, is_app_open, open_email, open_url, web_search, play_youtube,
         type_text, set_volume, media_control, now_playing, spotify_play, spotify_search, lock_screen, remember, install_app, run_shell]
NEEDS_CONFIRMATION = {"run_shell", "lock_screen", "install_app", "power_action", "close_all_apps"}

try:
    import google_skills
    TOOLS += google_skills.TOOLS
    NEEDS_CONFIRMATION |= google_skills.NEEDS_CONFIRMATION
except ImportError:
    pass

BY_NAME = {f.__name__: f for f in TOOLS}
