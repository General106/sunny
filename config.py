"""Settings for Sunny (formerly Jarvis). Edit these to taste."""
from pathlib import Path

HOME = Path(__file__).resolve().parent
DATA = HOME / "data"
DATA.mkdir(exist_ok=True)

ASSISTANT_NAME = "Sunny"
USER_NAME = "Your name"

# Local LLM served by Ollama. qwen2.5:7b handles tool calls well and fits
# (mostly) in the RTX 3050's 6 GB; drop to "qwen2.5:3b" if it feels slow.
LLM_MODEL = "qwen2.5:7b"

# Speech-to-text (faster-whisper). "small.en" is a good accuracy/speed balance on GPU.
WHISPER_MODEL = "small.en"
WHISPER_DEVICE = "auto"          # "auto" = GPU when plugged in, CPU on battery (battery mode)
BATTERY_MODE = True              # on battery: speech on CPU, fast mode off, GPU free to sleep
# Words Whisper should expect - add names of apps/people you often say.
WHISPER_HINT = ("Hey Sunny. Lost from Light. Sunny, open Spotify, YouTube, Chrome, Firefox, WhatsApp, Gmail, calendar, "
                "terminal, VS Code, volume, play, pause, next song, read my email, check my mail, "
                "YouTube Shorts, Claude, Claude AI.")

# Wake phrases, matched against what Whisper hears (so misheard variants are included).
# "Lost from Light" is Sunny's True Name in Shadow Slave.
WAKE_PATTERN = (r"\b(hey|hi|hay|okay|ok|yo),? (sunny|sonny|sunni|sunnie|soni|sunday)\b"
                r"|\blost (from|for|in|of|on) (the )?(light|lights|life)\b")
WAKE_PHRASE = "Hey Sunny"

# Voice recognition: only obey the enrolled speaker. Raise the threshold to be
# stricter, lower it if Sunny keeps ignoring you. Set False to disable.
REQUIRE_VOICE_MATCH = False
VOICE_MATCH_THRESHOLD = 0.72
VOICEPRINT_FILE = DATA / "voiceprint.npy"

# Text-to-speech (Piper) voice files, downloaded by setup.
PIPER_VOICE = DATA / "en_US-ryan-medium.onnx"   # was en_US-lessac-medium.onnx

# Audio capture
SAMPLE_RATE = 16000
SILENCE_SECONDS = 1.0            # stop recording after this much quiet
MAX_COMMAND_SECONDS = 15
# After answering, keep listening this long for a follow-up without the wake phrase.
FOLLOW_UP_SECONDS = 8

# Weather location (any city name wttr.in understands) and daily briefing time.
# Sunny speaks the briefing the first time it's running after this time each day.
HOME_CITY = ""                   # empty = auto-detect from your IP
BRIEFING_TIME = "08:00"          # set to "" to turn off
BRIEFING_DONE_FILE = DATA / "last_briefing.txt"

MEMORY_FILE = DATA / "memory.json"
HISTORY_TURNS = 12               # conversation turns kept in context
