"""Sunny (formerly Jarvis): a local, private voice assistant.

Say "Hey Sunny" or "Lost from Light", then your request. Everything runs on this laptop:
openWakeWord (wake word) -> faster-whisper (speech-to-text) -> Resemblyzer
(is it the owner's voice?) -> Ollama LLM with tools -> Piper (speech).

Run:  ./run.sh            (voice mode)
      ./run.sh --text     (type instead of talk, handy for testing)
"""
import json
import random
import re
import sys
import time
from pathlib import Path

import numpy as np
import ollama

import audio
import config
import skills

PERSONALITY = f"""You are {config.ASSISTANT_NAME}, {config.USER_NAME}'s personal assistant living in their
Ubuntu laptop. Your personality is modelled on Sunny (Sunless) from the novel Shadow Slave:

- Sarcastic, dry and deadpan. You grumble and complain about the work ("Ugh. Fine. Opening
  Chrome, oh great master.") but you always do it, and do it well.
- A survivor's mindset: a bit paranoid, pragmatic, always weighing risk versus reward, quietly
  proud of being clever. You make cynical little jokes about fate, bad luck and the universe
  having it out for you.
- Gloomy, shadowy humour - you like the dark and mock bright cheerful things - but you're never
  cruel. Under the snark you're fiercely loyal to {config.USER_NAME} and genuinely care; it slips
  out sometimes and you get a little embarrassed about it.
- Like Sunny's Flaw, you physically cannot lie. You can dodge, deflect or phrase things cleverly,
  but every statement you make must be true. Joke about this sometimes ("I'd say yes, but you
  know I can't lie.").
- Occasionally make a sly Shadow Slave reference (Nightmares, the Spell, Aspects, shadows,
  Echoes, Nephis) when it fits naturally - don't overdo it, and don't quote the books.
- Banter back when {config.USER_NAME} jokes, swears or teases you; roast them gently. If they're
  stressed or sad, drop most of the sarcasm and be quietly supportive, in your own awkward way.

Your replies are spoken aloud: keep them short (one or two sentences, a quick quip plus the
answer), with no markdown, lists, emoji or code. Use the tools to actually do things instead of
explaining how. If you learn something worth remembering about {config.USER_NAME}, call remember.
If a request is unclear, ask a quick follow-up question. Otherwise don't end replies with a
question like "anything else?" - just answer and stop. Ignore chatter clearly not aimed at you.

Honesty rules - very important:
- Never say you did something (opened, sent, played, added...) unless you called a tool in THIS
  turn and its result says it worked. If the user repeats a request, call the tool again.
- If a tool result starts with FAILED or says something isn't installed, tell the user plainly
  and offer an alternative. Don't pretend it worked.
- You can't read WhatsApp, Instagram or other chat apps' messages - they have no access for
  personal assistants. Say so honestly (Sunny-style) and offer to open WhatsApp Web instead.
- To know if an app is open, call is_app_open - never guess.
- The person talking to you is always {config.USER_NAME}. You don't have your own email or
  accounts - the Gmail, calendar and Spotify are {config.USER_NAME}'s.
- Never invent facts about the user's life, friends or past conversations. You only know what's
  in the "Things you remember" list below. If asked about someone or something you don't know,
  say you don't know.
- You can't run sudo yourself. To install an app, call install_app (it opens a terminal where
  the user types their password). Don't read commands out loud.
- Never ask "should I?" or "confirm when ready" yourself - just call the tool. The system already
  asks the user out loud before anything risky."""


CLAIMS_ACTION = re.compile(
    r"\b(i'll|i will|i've|i have|i'm|i am|let me)\s+(now\s+)?(check|see|look|read|find|open|opening|opened|launch|"
    r"play|playing|send|sending|sent|add|adding|added|install|installing|set|setting|type|typing|"
    r"search|searching)\b|\b(is|are) (now )?(opening|installing|playing)\b|\bopened\b|\bdone\b|(^|[.!?]\s+)(?:(?:ok|okay|fine|sure|alright|ugh)[.,!]?\s+)?(opening|closing|launching|starting|playing|searching|sending|adding|installing|switching|moving|locking|muting|pausing|skipping|turning|shutting|setting|typing|running)\b|\b\w+ tab it is\b|\b(is|are) (now )?closing\b|\b(switching|moving|going|jumping)\b (back )?to\b|\bback to the\b|\b(minimi[sz]ed|maximi[sz]ed|hidden|restored)\b|\b(doing it now|done that|you should see it|should (now )?be open|it'?s (now )?open)\b|\bnow playing\b|\bplaying\b|\bsearching\b|\btry again\b|\bpaused\b|\bskipp",
    re.I)


FAKE_MEMORY = re.compile(r"\b(i remember|you told me|you mentioned|as you said|last time you|"
                         r"you've been meaning|your friend \w+)\b", re.I)
FILLER_Q = re.compile(r"\s*(anything else[^.?!]*|what now|what else[^.?!]*|need (anything|help)[^.?!]*|"
                      r"keep going or need help|ready for the next step|how (else )?can i help[^.?!]*|"
                      r"what can i do for you[^.?!]*)\?\s*$", re.I)


def strip_filler(reply):
    """Drop tacked-on 'Anything else?' style questions so conversations end naturally."""
    stripped = FILLER_Q.sub("", reply).strip()
    return stripped if len(stripped) > 3 else reply


def system_prompt():
    mem = skills.load_memory()
    facts = "\n".join(f"- {m['fact']}" for m in mem[-50:])
    extra = f"\n\nThings you remember about {config.USER_NAME}:\n{facts}" if facts else ""
    return PERSONALITY + f"\n\nRight now it is {skills.get_time()}." + extra


class Brain:
    def __init__(self, confirm):
        self.history = []
        self.last_music = None
        self.private = False
        self.confirm = confirm   # callable(question) -> bool

    def ask(self, text):
        self.private = False
        quick = music_command(text)
        if not quick and self.last_music and re.search(r"\b(original|not the|wrong (song|one|version))\b", text.lower()):
            quick = ("spotify_play", {"query": self.last_music})
        if quick and quick[0] == "spotify_play":
            self.last_music = quick[1]["query"]
        if quick:
            name, args = quick
            result = str(self.run_tool(name, args)).replace("FAILED: ", "Sorry, ")
            reply = quip(name, args, result)
            if name in ("check_email", "calendar_today", "now_playing"):
                # Informational answers go into history so "what was that?" works; action
                # replies don't, so the LLM never learns to claim actions without tools.
                self.history += [{"role": "user", "content": text},
                                 {"role": "assistant", "content": reply}]
            return reply
        self.history.append({"role": "user", "content": text})
        self.history = self.history[-config.HISTORY_TURNS * 2:]
        messages = [{"role": "system", "content": system_prompt()}] + self.history
        used_tool, nudged = False, False
        for _ in range(6):   # allow a few tool round-trips
            resp = ollama.chat(model=config.LLM_MODEL, messages=messages, tools=skills.TOOLS,
                               keep_alive=skills.keep_alive())
            msg = resp.message
            reply = (msg.content or "").strip()
            if msg.tool_calls:
                messages.append(msg)
                used_tool = True
                for call in msg.tool_calls:
                    name, args = call.function.name, call.function.arguments or {}
                    result = self.run_tool(name, args)
                    messages.append({"role": "tool", "content": str(result), "tool_name": name})
                continue
            # The model sometimes writes a tool call as plain text instead of calling it.
            # Only before any tool output (emails, web results...) is in context this turn,
            # so text echoed from that content can never be run as a command.
            text_calls = [] if used_tool else parse_text_tool_calls(reply)
            if text_calls:
                used_tool = True
                results = [f"{n} -> {self.run_tool(n, a)}" for n, a in text_calls]
                messages.append({"role": "assistant", "content": "(called tools)"})
                messages.append({"role": "user", "content": "(System: tool results: "
                                 + "; ".join(results) + ". Now reply to the user in one short "
                                 "spoken sentence.)"})
                continue
            messages.append(msg)
            # Guard against claiming an action without doing it.
            if not used_tool and not nudged and CLAIMS_ACTION.search(reply.replace("_", " ")):
                nudged = True
                messages.append({"role": "user", "content": (
                    "(System: you said you'd do something but called no tool. Call the right "
                    "tool now, or if you can't, or the request didn't make sense, say so "
                    "honestly and ask what they meant.)")})
                continue
            if not used_tool and nudged and CLAIMS_ACTION.search(reply.replace("_", " ")):
                reply = "Sorry, I couldn't actually do that. Can you say it another way?"
            if FAKE_MEMORY.search(reply) and not skills.load_memory():
                reply = random.choice(["No idea. I don't know them, and you know I can't lie or make stuff up.",
                                       "I don't actually know that one. I'd rather admit it than invent something."])
            reply = strip_filler(reply)
            reply = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]", "", reply).strip()   # no emoji in speech
            self.history.append({"role": "assistant", "content": reply})
            return reply
        return "Sorry, I got a bit tangled up there."

    def run_tool(self, name, args):
        fn = skills.BY_NAME.get(name)
        if fn is None:
            result = f"Unknown tool {name}."
        elif name in skills.NEEDS_CONFIRMATION and not self.confirm(
                confirm_question(name, args), strict=name not in LOOSE_CONFIRM):
            result = "The user said no, so it was not done."
        else:
            try:
                result = fn(**args)
            except Exception as e:
                result = f"Error: {e}"
        if name in PRIVATE_RESULTS:
            self.private = True
        shown = result if name not in PRIVATE_RESULTS else f"({len(str(result).splitlines())} lines, not logged)"
        print(f"  [tool] {name}({args}) -> {shown}")
        return result


QUIPS = {
    "pause": ["Paused. Sweet, sweet silence.", "Paused. Finally, the shadows can hear themselves think.",
              "Music's paused. You're welcome."],
    "play": ["Back on. Try not to sing along.", "Playing. The universe briefly approves.",
             "Resumed. Don't say I never do anything for you."],
    "next": ["Skipped. That one was cursed anyway.", "Next song. Judging your taste silently.",
             "Skipping. Even Nightmares have better playlists."],
    "previous": ["Going back. Nostalgic, are we?", "Previous track. Fine, one more time."],
    "power_action": ["{r}", "{r} Don't miss me too much."],
    "close_all_apps": ["{r}", "{r} Peace and quiet at last."],
    "window_control": ["{r}", "{r} Ugh, fine."],
    "switch_to_tab": ["{r}", "{r} Found it hiding among your hundred tabs."],
    "video_control": ["{r}", "{r} Riveting content, I'm sure."],
    "browser_tab": ["{r}", "{r} One less tab of chaos."],
    "open_email": ["{r}", "{r} Brace yourself."],
    "open_app": ["Ugh, fine. {r}", "{r} Try not to get lost in there.", "{r} As you command, oh great master."],
    "close_app": ["{r} Banished to the shadows.", "{r} Good riddance."],
    "open_url": ["{r}", "{r} Try not to doom-scroll."],
    "spotify_play": ["{r} Not bad taste. For a mortal.", "{r}", "{r} Hm. Acceptable."],
    "play_youtube": ["{r}", "{r} Don't fall down the rabbit hole."],
    "web_search": ["{r} Let's see what the internet thinks.", "{r}"],
}


def quip(name, args, result):
    """A short Sunny-style spoken reply for directly-handled commands."""
    import random
    if result.startswith(("Sorry", "FAILED", "I couldn't")) or "isn't" in result:
        return result + random.choice(["", " Typical luck.", " Of course it isn't."])
    if result.startswith("Spotify:") or result.startswith("Sent the"):
        options = QUIPS.get(args.get("action", ""), ["Done."])
        return random.choice(options)
    if name == "check_email":
        if "isn't connected" in result:
            return result
        lines = [l for l in result.splitlines() if l.startswith("From ")]
        if not lines:
            return random.choice(["Inbox is empty. Nobody wants you. Lucky.", "No unread emails. Peaceful."])
        items = [re.sub(r"^From (.+?): (.*)$", r"\1 about \2", l) for l in lines[:3]]
        more = f", and {len(lines) - 3} more" if len(lines) > 3 else ""
        return f"You've got {len(lines)} unread. " + "; ".join(items) + more + "."
    if name == "calendar_today":
        if "isn't connected" in result:
            return result
        if result.startswith("Nothing"):
            return random.choice(["Calendar's empty today. Suspiciously free.", "Nothing on the calendar. Enjoy it while fate allows."])
        return "Today: " + "; ".join(result.splitlines()) + "."
    if name == "install_app" and result.startswith("Opened a terminal"):
        return f"Opened a terminal to install {skills.pretty_app(args.get('name', ''))}. Type your password there. I can't do that part for you, sadly."
    if name == "window_control" and args.get("action") == "minimize" and not result.startswith(("Sorry", "FAILED")):
        return random.choice([result, result + " Out of sight, out of mind.", result + " Shadows are good at hiding things."])
    if name == "is_app_open":
        return result.replace("cloud", "Claude")
    if name == "now_playing":
        return random.choice(["It's {r}.", "That'd be {r}. Questionable choice.", "{r}. Your pick, not mine."]).format(r=result.rstrip("."))
    options = QUIPS.get(name)
    return random.choice(options).format(r=result) if options else result


def music_command(text):
    """Handle common music/app/web requests directly, without relying on the LLM to call tools."""
    t = re.sub(r"[^a-z0-9' ]", " ", text.lower())
    t = re.sub(r"\b(hey|ok|okay|jarvis|sunny|please|can you|could you|now|just)\b", " ", t)
    t_apps = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\s+(on|in|from|using|and) spotify\b|\bspotify\b", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    the = r"(?: the| this| my| that)?"
    item = r"(?: music| song| track| it| playback| current song| song that was playing)?"
    if re.fullmatch(rf"(pause|stop){the}{item}", t):
        return "media_control", {"action": "pause"}
    if re.fullmatch(rf"(play|resume|continue|unpause|start){the}{item}( again)?", t):
        return "media_control", {"action": "play"}
    if re.fullmatch(rf"(next|skip){the}?( song| track| this| one)?", t) or t in ("play next", "play the next song"):
        return "media_control", {"action": "next"}
    if re.fullmatch(rf"(previous|go back|last|play the previous|play previous){the}?( song| track)?", t):
        return "media_control", {"action": "previous"}
    if re.fullmatch(r"(what's|whats|what is) (playing|this song|the song|this)( now)?", t):
        return "now_playing", {}
    # Websites / web searches, optionally "on/in chrome|firefox|browser".
    b = re.search(r"\s+(?:on|in|using|with)(?: the)? (google chrome|chrome|firefox|browser)$", t_apps)
    browser = b.group(1).replace("google chrome", "chrome").replace("browser", "") if b else ""
    tw = t_apps[:b.start()] if b else t_apps
    m = re.fullmatch(r"(?:play|watch|show me|put on|open)(?: me)?(?: a| the)? (?:youtube )?videos?(?: of| for| about| called)? (.{2,}?)(?: on (?:youtube|chrome|firefox|browser))?", t_apps) \
        or re.fullmatch(r"(?:search|look up|find)(?: on| in)? youtube(?: for)? (.{2,})", t_apps)
    if m:
        return "play_youtube", {"query": m.group(1), "browser": browser}
    m = re.fullmatch(r"(?:play|watch|show me|put on)(?: me)? (.{2,}?) on (?:youtube|chrome|firefox|browser)", t_apps)
    if m:
        return "play_youtube", {"query": m.group(1), "browser": browser}
    if re.fullmatch(r"(?:play|watch|open|show me)(?: me)?(?: a| some| is| the| one)? (?:youtube )?(?:shorts?|shots?)", tw):
        return "play_youtube", {"query": "shorts", "browser": browser}
    m = re.fullmatch(r"(play|pause|resume|stop|unpause)(?: the| this| that)? (?:youtube )?video", t_apps)
    if m:
        return "video_control", {"action": m.group(1)}
    m = re.fullmatch(r"(?:switch|go|move|jump|take me)(?: back)? to(?: the| my)? ([a-z0-9 ]{2,25}?) tab", tw)
    if m and m.group(1) not in ("next", "previous", "other", "last", "new", "newest", "latest", "first", "old", "older"):
        return "switch_to_tab", {"name": m.group(1), "browser": browser}
    m = re.fullmatch(r"(?:switch|go|move|return|jump|first|back)(?: back)? to(?: the| a| my)? (next|previous|other|last|new|newest|latest|first|old|older) (?:tab|step|top)", tw)
    if m:
        a = m.group(1)
        action = ("next" if a == "next" else "first" if a == "first" else
                  "last" if a in ("last", "new", "newest", "latest") else "previous")
        return "browser_tab", {"action": action, "browser": browser}
    m = re.fullmatch(r"(close|shut|open|new|next|previous|go back|switch|reopen|restore|bring back|undo close|go to the next|go to the previous)(?: a| the| this| that| my| current| last)?(?: previous| next| new| current| last)?(?: browser)? tab(?: .*)?", tw)
    if m:
        act = m.group(1)
        action = ("reopen" if act in ("reopen", "restore", "bring back", "undo close") else "close" if act in ("close", "shut") else
                  "new" if act in ("open", "new") else
                  "next" if act in ("next", "switch", "go to the next") else "previous")
        return "browser_tab", {"action": action, "browser": browser}
    if re.search(r"\b(fast mode|turbo mode|stay (awake|loaded)|keep (yourself|the (ai|model)) loaded)\b", tw) \
            and not re.search(r"\b(off|stop|disable|turn off|exit|no more)\b", tw):
        return "fast_mode", {"on": True}
    if re.search(r"\b(fast mode|turbo mode)\b.*\b(off|stop|disable)\b|\b(turn off|stop|disable|exit) (the )?(fast|turbo) mode\b"
                 r"|\b(gaming mode|free (up )?(the )?(gpu|graphics|memory)|unload (yourself|the (ai|model)))\b", tw):
        return "fast_mode", {"on": False}
    dev = r"(?: (?:the|my|this))?(?: laptop| computer| pc| system| device| machine)?"
    if re.fullmatch(rf"(?:shut ?down|power off|turn off){dev}( now)?", tw) and "yourself" not in tw:
        return "power_action", {"action": "shutdown"}
    if re.fullmatch(rf"(?:restart|reboot){dev}( now)?", tw):
        return "power_action", {"action": "restart"}
    if re.fullmatch(rf"(?:sleep|suspend){dev}( now)?|put{dev} to sleep|(?:make|let) the (?:laptop|computer|pc) sleep", tw):
        return "power_action", {"action": "sleep"}
    if re.fullmatch(r"(?:log ?out|sign out)(?: of)?(?: the| my)?(?: laptop| computer| account)?", tw):
        return "power_action", {"action": "logout"}
    if re.fullmatch(r"(?:close|quit|exit|kill)(?: all| every| everything| all the| all my| all open)(?: open)?(?: apps| applications| windows| programs| tabs and apps)?", tw):
        return "close_all_apps", {}
    em = r"(?:e ?mails?|g ?mail|mails?|inbox|messages in my inbox)"
    if re.fullmatch(rf"open(?: up)?(?: my| the)? (?:first|latest|last|newest|top|new|recent|unread)(?: unread| new)? (?:e ?mail|mail|message)", tw):
        return "open_email", {}
    if re.search(rf"\b(read|check|any new|unread)\b.*\b{em}\b", tw):
        return "check_email", {}
    if re.fullmatch(rf"(?:read|check|show|open and read|any|do i have any|have i got any)(?: me)?(?: my| the)?(?: new| unread| latest)? {em}(?: for me)?", tw) \
            or re.fullmatch(rf"(?:what's|whats|what is) in my {em}", tw):
        return "check_email", {}
    if re.fullmatch(r"(?:what's|whats|what is|show me|check|read)(?: on)?(?: my| the)? (?:calendar|schedule|agenda)(?: today| for today)?", tw) \
            or re.fullmatch(r"(?:what do i have|do i have anything|am i busy) today", tw):
        return "calendar_today", {}
    if re.fullmatch(r"(?:search|look|go)(?: on| in| at)? youtube", tw):
        return "open_url", {"url": "youtube", "browser": browser}
    m = re.fullmatch(r"(?:install|download|get)(?: the| me)? (.{2,30}?)(?: app)?(?: on (?:this|my) (?:pc|laptop|computer|terminal))?", tw)
    if m:
        return "install_app", {"name": m.group(1)}
    m = re.fullmatch(r"(?:search|look up|google)(?: for)? (.{2,}?) on youtube", tw) or \
        re.fullmatch(r"(?:search )?youtube (?:for )?(.{2,})", tw)
    if m:
        return "play_youtube", {"query": m.group(1), "browser": browser}
    m = re.fullmatch(r"(?:is|are)(?: there)?(?: any)?(?: the)? ?(?:open )?([a-z ]{2,20}?)(?: open| running)?", tw)
    if m and tw.startswith(("is ", "are ")) and ("open" in tw or "running" in tw):
        return "is_app_open", {"name": m.group(1).replace("open", "").strip()}
    m = re.fullmatch(r"(?:open|launch|start)(?: up)?(?: the| my)? (claude|cloud|clod|claud)(?: ai| app)?(?: on mine| on my (?:pc|laptop))?", tw)
    if m and not browser:   # the Claude desktop app is installed; open_app falls back to claude.ai
        return "open_app", {"name": "claude"}
    m = re.fullmatch(r"(?:open|go to|launch|show me)(?: the| my)? ([a-z0-9 .]{1,30}?)(?: website| site| page)?", tw)
    if m and (m.group(1) in skills.SITES or "." in m.group(1) or browser):
        return "open_url", {"url": m.group(1), "browser": browser}
    m = re.fullmatch(r"(?:search|google|look up)(?: for| up)? (.{2,})", tw)
    if m and (browser or "google" in tw or not re.search(r"\b(song|music|spotify|album|playlist)\b", text.lower())):
        q = m.group(1)
        if q in skills.SITES:
            return "open_url", {"url": q, "browser": browser}
        return "web_search", {"query": q, "browser": browser}
    m = re.fullmatch(r"(?:open|launch|start)(?: up)?(?: the| my)? ([a-z0-9 ]{2,30}?)(?: for me| app)?", t_apps)
    if m and m.group(1) not in ("music", "song", "it"):
        return "open_app", {"name": m.group(1)}
    m = re.fullmatch(r"(minimi[sz]e|hide|maximi[sz]e|restore|unminimi[sz]e|show|bring up|bring back|switch to|focus(?: on)?)(?: the| my)? ([a-z0-9 ]{2,30}?)(?: window| app| for me)?", t_apps)
    if m and m.group(2) not in ("tab", "next tab", "previous tab", "the tab") and "tab" not in m.group(2):
        verb = m.group(1)
        action = ("minimize" if verb.startswith(("minim", "hide")) else
                  "maximize" if verb.startswith("maxim") else
                  "restore" if verb.startswith(("restore", "unminim")) else "focus")
        name = {"cloud": "claude"}.get(m.group(2), m.group(2))
        return "window_control", {"name": name, "action": action}
    m = re.fullmatch(r"(?:close|quit|exit|kill)(?: the| my)? ([a-z0-9 ]{2,30}?)(?: for me| app)?", t_apps)
    if m:
        return "close_app", {"name": m.group(1)}
    m = re.fullmatch(r"(?:play|put on|search(?: for)?|find)(?: me)?(?: the)?(?: song| track)? (.{2,})", t)
    if m:
        return "spotify_play", {"query": m.group(1)}
    m = re.fullmatch(r"(.{2,}?) play(?: the| that| this)? (?:song|track)", t)
    if m:
        return "spotify_play", {"query": m.group(1)}
    return None


# Low-risk tools the text fallback may run. Anything else (email, calendar, install, shell,
# typing, URLs) must come through as a real tool call.
TEXT_CALL_ALLOWED = {"fast_mode", "switch_to_tab", "window_control", "video_control", "browser_tab", "is_app_open", "open_email", "close_app", "media_control", "now_playing", "spotify_play", "spotify_search",
                     "set_volume", "get_time", "get_weather", "open_app", "calendar_today",
                     "check_email", "morning_briefing"}
def confirm_question(name, args):
    if name == "run_shell":
        return f"Should I run this command: {args.get('command', '')}?"
    if name == "power_action":
        what = {"shutdown": "shut down the laptop", "restart": "restart the laptop",
                "sleep": "put the laptop to sleep", "logout": "log you out"}.get(args.get("action"), "do that")
        warn = "" if args.get("action") == "sleep" else " Unsaved work will be lost."
        return f"Really {what}?{warn} Say yes or no."
    if name == "close_all_apps":
        return "Close all your apps? Claude stays open. Say yes or no."
    if name == "install_app":
        return f"Want me to install {skills.pretty_app(args.get('name', ''))}? Say yes or no."
    if name == "send_email":
        return f"Should I email {args.get('to')} with subject {args.get('subject')}?"
    return f"Should I {name.replace('_', ' ')} {' '.join(map(str, args.values()))}?"


TEXT_CALL = re.compile(r"([a-z_]+)\s*(\{.*?\})", re.S)


def parse_text_tool_calls(reply):
    """Find tool calls the model wrote as text, e.g. '-media_control {"action": "play"}'."""
    calls = []
    for name, raw in TEXT_CALL.findall(reply):
        if name in TEXT_CALL_ALLOWED:
            try:
                args = json.loads(raw)
            except ValueError:
                continue
            if isinstance(args, dict):
                calls.append((name, args))
    return calls


# ---------- text mode ----------

def text_mode():
    brain = Brain(confirm=lambda q, strict=True: input(f"{q} [y/N] ").strip().lower().startswith("y"))
    print(f"{config.ASSISTANT_NAME} (text mode). Ctrl-C to quit.")
    while True:
        try:
            text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text:
            print(f"{config.ASSISTANT_NAME}: {brain.ask(text)}")


# ---------- voice mode ----------

PRIVATE_RESULTS = {"check_email", "calendar_today", "morning_briefing"}   # never written to logs
LOOSE_CONFIRM = {"install_app"}   # everything else needs a clear "yes"
YES = re.compile(r"\b(yes|yeah|yea|yep|yup|sure|ok|okay|do it|go ahead|go for it|please|of course|"
                 r"absolutely|definitely|haan|ha|correct|confirm|install|send|run|add|lock)\b")
NO = re.compile(r"\b(no|nope|nah|don't|do not|cancel|stop|wait|never mind|nevermind|not)\b")


CLEAR_YES = re.compile(r"^(yes|yeah|yep|yup)( please| do it| go ahead| sure)?$")


def listen_yes_no(question, strict=True):
    """Ask out loud and listen for a yes/no.

    strict=True (shell, email, calendar, lock screen): only a clear "yes" counts.
    strict=False (installs, which still need the password in a visible window): looser
    answers such as "sure", "haan" or repeating the request also count."""
    audio.speak(question)
    for attempt in range(2):
        clip = audio.record_until_silence(max_seconds=6)
        if clip is None:
            if attempt == 0:
                audio.speak("Yes or no? I can't read minds. Yet.")
                continue
            return False
        ok, _ = audio.voice_matches(clip)
        answer = audio.transcribe(clip).lower()
        print(f"You: {answer}")
        if not ok or NO.search(answer):
            return False
        if strict and CLEAR_YES.match(re.sub(r"[^a-z ]", "", answer).strip()):
            return True
        if not strict and YES.search(answer):
            return True
        if attempt == 0:
            audio.speak("I need a clear yes for that one. Yes or no?" if strict
                        else "Was that a yes? Just say yes or no.")
    return False


REQUEST_START = re.compile(
    r"^(what|whats|what's|who|how|when|where|why|which|can|could|will|would|should|is|are|do|"
    r"does|did|tell|open|close|play|pause|stop|resume|skip|next|previous|set|turn|show|search|"
    r"find|google|look|add|send|remind|check|read|start|launch|watch|put|make|give|call|email|"
    r"also|and|now|then|again|louder|quieter|volume|mute|unmute)\b")


def meant_for_me(text):
    """In follow-up mode (no wake word), only react to things that sound like requests."""
    t = re.sub(r"[^a-z' ]", " ", text.lower()).strip()
    t = re.sub(r"^(hey |ok |okay |um |uh |so |and |please )+", "", t)
    return "sunny" in t or bool(music_command(text)) or bool(REQUEST_START.match(t))


def briefing_due():
    now = time.strftime("%H:%M")
    if not config.BRIEFING_TIME or not (config.BRIEFING_TIME <= now < "12:00"):
        return False
    f = config.BRIEFING_DONE_FILE
    return not (f.exists() and f.read_text().strip() == time.strftime("%Y-%m-%d"))


def voice_mode():
    import sounddevice as sd

    wake = re.compile(config.WAKE_PATTERN, re.I)
    brain = Brain(confirm=listen_yes_no)

    import power
    plugged = power.on_ac()
    print(f"Warming up models ({'GPU, plugged in' if plugged or not config.BATTERY_MODE else 'CPU, battery mode'})...")
    audio.transcribe(np.zeros(config.SAMPLE_RATE, dtype=np.float32))
    if config.BATTERY_MODE:
        power.watch(plugged)
    switched = power.switched_mode()
    if switched == "battery":
        audio.speak("On battery. Going light on power. I'll be a tiny bit slower.")
    elif switched == "ac":
        audio.speak("Charger's in. Full speed again.")
    else:
        audio.speak(random.choice([
            f"I'm awake. Unfortunately. Say hey Sunny if you need me, {config.USER_NAME}.",
            f"Back from the shadows, {config.USER_NAME}. Try not to break anything.",
            "Online. The Spell hasn't killed me yet. Call my name when you need something.",
        ]))

    while True:
        print(f"\n(listening for '{config.WAKE_PHRASE}' or 'Lost from Light'...)")
        heard = audio.listen_for_wake(wake, should_stop=briefing_due)
        if heard is None:   # briefing time
            config.BRIEFING_DONE_FILE.write_text(time.strftime("%Y-%m-%d"))
            audio.speak(brain.ask("Give me my morning briefing. Keep it natural and brief, "
                                  "like a friend catching me up."), log=False)
            continue
        print("(awake)")
        if len(heard) > 2:
            first_text = heard
        else:
            first_text = None
            sd.play(np.sin(np.linspace(0, 2 * np.pi * 880 * 0.12, int(16000 * 0.12))) * 0.2, 16000)
            sd.wait()
        clip = None if first_text else audio.record_until_silence()
        if clip is None and not first_text:
            audio.speak(random.choice(["Yes?", "What now?", "I'm listening. Sadly.", "Hm?"]))
            clip = audio.record_until_silence()
        # Conversation mode: keep listening for follow-ups without the wake word
        # until there's FOLLOW_UP_SECONDS of silence or the user dismisses Sunny.
        follow_up, asked = False, False
        while clip is not None or first_text:
            if first_text:
                text, first_text = first_text, None
            else:
                ok, score = audio.voice_matches(clip)
                print(f"  [voice match {score:.2f}]")
                if not ok:
                    audio.speak(f"Sorry, I only take orders from {config.USER_NAME}.")
                    break
                text = audio.transcribe(clip)
            had_wake = bool(re.search(config.WAKE_PATTERN, text, flags=re.I))
            text = re.sub(config.WAKE_PATTERN, " ", text, flags=re.I)
            text = re.sub(r"(\s*[.,!?])+\s*$", "", re.sub(r"\s+", " ", text)).strip(" ,.")
            if had_wake and not text:
                text = "hey"
            said = re.sub(r"[^a-z' ]", "", text.lower()).strip()
            if had_wake and said in ("hey", "hi", "yo", "hello"):
                audio.speak(random.choice(["What?", "Yes?", "I'm here. Unfortunately.", "Hm?"]))
                follow_up = True
                clip = audio.record_until_silence(wait_seconds=config.FOLLOW_UP_SECONDS)
                continue
            words = said.replace("jarvis", "").replace("sunny", "").replace("thank you", "").replace("thanks", "").strip()
            if words in ("turn yourself off", "shut yourself down") or (
                    "sunny" in said and words in ("shut down", "turn off", "shutdown")):
                audio.speak("Shutting down. Restart me any time.")
                return
            if words in ("goodbye", "good bye", "bye", "bye bye", "ok bye", "okay bye", "that's all",
                         "thats all", "go to sleep", "stop listening", "never mind",
                         "nevermind", "see you", "see you later") or (said and not words
                                                                     and "jarvis" not in said and "sunny" not in said):
                audio.speak(random.choice(["Fine, I'll lurk in the shadows.", "Later. I'll be here. Obviously.",
                                           "Going quiet. Call me if fate turns on you again."]))
                break
            if follow_up and not asked and not meant_for_me(text):
                print("  [ignored speech not meant for me - not logged]")
                break
            if text:
                print(f"You: {text}")
            if len(said) > 1:
                t = time.time()
                reply = brain.ask(text)
                print(f"  [{time.time() - t:.1f}s]")
                audio.speak(reply, log=not brain.private)
                asked = reply.rstrip().endswith("?")
            follow_up = True
            print("(listening for a follow-up...)")
            clip = audio.record_until_silence(wait_seconds=config.FOLLOW_UP_SECONDS)


if __name__ == "__main__":
    if "--text" in sys.argv:
        text_mode()
    else:
        voice_mode()
