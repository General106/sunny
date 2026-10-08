"""Teach Jarvis what your voice sounds like. Run once (re-run any time to redo it)."""
import numpy as np

import audio
import config

PHRASES = [
    "Hey Jarvis, what's the weather like today?",
    "Open my browser and search for the latest news.",
    "Remind me to call my family this evening.",
    "Play some music and turn the volume up a little.",
    "The quick brown fox jumps over the lazy dog.",
    "Jarvis, tell me something interesting about space.",
]

print("Voice enrollment: read each sentence aloud in your normal voice.\n")
embeddings = []
for i, phrase in enumerate(PHRASES, 1):
    input(f"[{i}/{len(PHRASES)}] Press Enter, then say:\n    \"{phrase}\"")
    clip = audio.record_until_silence(max_seconds=10)
    if clip is None:
        print("   didn't hear anything, skipping")
        continue
    e = audio.embed(clip)
    if e is not None:
        embeddings.append(e)
        print("   got it")

if len(embeddings) < 3:
    raise SystemExit("Not enough clean recordings - please run this again.")

voiceprint = np.mean(embeddings, axis=0)
voiceprint /= np.linalg.norm(voiceprint)
np.save(config.VOICEPRINT_FILE, voiceprint)
sims = [float(np.dot(e, voiceprint) / np.linalg.norm(e)) for e in embeddings]
print(f"\nSaved your voiceprint ({len(embeddings)} samples, self-match {min(sims):.2f}-{max(sims):.2f}).")
print(f"Jarvis will accept voices scoring above {config.VOICE_MATCH_THRESHOLD} (set in config.py).")
