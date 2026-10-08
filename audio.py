"""Microphone capture, speech-to-text, text-to-speech and voice matching."""
import wave
import io

import numpy as np
import sounddevice as sd

import config

_whisper = None
_piper = None
_encoder = None
_voiceprint = None


# ---------- microphone ----------

def record_until_silence(max_seconds=config.MAX_COMMAND_SECONDS, wait_seconds=5):
    """Record from the default mic until the speaker pauses. Returns float32 mono."""
    sr = config.SAMPLE_RATE
    block = int(sr * 0.05)
    chunks, quiet, heard = [], 0.0, False
    # Calibrate the noise floor from the first few blocks.
    floor = None
    with sd.InputStream(samplerate=sr, channels=1, dtype="float32", blocksize=block) as stream:
        for _ in range(int(max_seconds / 0.05)):
            data, _ = stream.read(block)
            data = data[:, 0].copy()
            chunks.append(data)
            level = float(np.sqrt(np.mean(data ** 2)))
            if floor is None or len(chunks) <= 4:
                floor = level if floor is None else min(floor, level)
                continue
            if level > max(floor * 3, 0.01):
                heard, quiet = True, 0.0
            else:
                quiet += 0.05
            if heard and quiet >= config.SILENCE_SECONDS:
                break
            if not heard and len(chunks) * 0.05 > wait_seconds:   # nothing said in time
                break
    audio = np.concatenate(chunks)
    return audio if heard else None


# ---------- speech-to-text ----------

def transcribe(audio, quick=False):
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel
        import power
        device = config.WHISPER_DEVICE
        if device == "auto":
            device = "cuda" if (power.on_ac() or not config.BATTERY_MODE) else "cpu"
        try:
            if device == "cpu":
                raise RuntimeError("battery mode")
            _whisper = WhisperModel(config.WHISPER_MODEL, device=device,
                                    compute_type="int8_float16")
        except Exception as e:
            print(f"[stt] speech recognition on CPU ({e})")
            _whisper = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8",
                                    cpu_threads=4)
    if quick:   # cheap pass for wake-phrase checks: greedy, no extra VAD model
        segments, _ = _whisper.transcribe(audio, language="en", beam_size=1, vad_filter=False,
                                          initial_prompt=config.WHISPER_HINT,
                                          condition_on_previous_text=False)
    else:
        segments, _ = _whisper.transcribe(audio, language="en", beam_size=3, vad_filter=True,
                                          initial_prompt=config.WHISPER_HINT)
    return " ".join(s.text.strip() for s in segments).strip()


# ---------- text-to-speech ----------

def speak(text, log=True):
    global _piper
    print(f"{config.ASSISTANT_NAME}: {text if log else '(private reply, not logged)'}")
    if not text:
        return
    if _piper is None:
        from piper import PiperVoice
        _piper = PiperVoice.load(str(config.PIPER_VOICE))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        if hasattr(_piper, "synthesize_wav"):       # piper-tts >= 1.3
            _piper.synthesize_wav(text, wf)
        else:
            _piper.synthesize(text, wf)
    buf.seek(0)
    with wave.open(buf, "rb") as wf:
        sr = wf.getframerate()
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    sd.play(pcm, sr)
    sd.wait()


# ---------- voice recognition ----------

def _get_encoder():
    global _encoder
    if _encoder is None:
        from resemblyzer import VoiceEncoder
        _encoder = VoiceEncoder("cpu", verbose=False)
    return _encoder


def embed(audio):
    from resemblyzer import preprocess_wav
    wav = preprocess_wav(audio, source_sr=config.SAMPLE_RATE)
    if len(wav) < config.SAMPLE_RATE * 0.5:
        return None
    return _get_encoder().embed_utterance(wav)


def voice_matches(audio):
    """True if `audio` sounds like the enrolled user (or matching is disabled)."""
    global _voiceprint
    if not config.REQUIRE_VOICE_MATCH:
        return True, 1.0
    if _voiceprint is None:
        if not config.VOICEPRINT_FILE.exists():
            print("[voice] no voiceprint enrolled yet - run enroll_voice.py; accepting anyone")
            return True, 1.0
        _voiceprint = np.load(config.VOICEPRINT_FILE)
    e = embed(audio)
    if e is None:
        return False, 0.0
    score = float(np.dot(e, _voiceprint) / (np.linalg.norm(e) * np.linalg.norm(_voiceprint)))
    return score >= config.VOICE_MATCH_THRESHOLD, score


# ---------- wake phrase (Whisper-based, so any phrase works) ----------

def listen_for_wake(pattern, should_stop=lambda: False, max_clip=6.0):
    """Block until speech matching `pattern` (a compiled regex) is heard.

    Returns the text spoken after the wake phrase in the same breath ("" if none),
    or None if should_stop() became true (checked about every 30 s).
    """
    sr = config.SAMPLE_RATE
    block = int(sr * 0.05)
    floor, pre, clip, quiet, blocks = None, [], [], 0.0, 0
    with sd.InputStream(samplerate=sr, channels=1, dtype="float32", blocksize=block) as stream:
        while True:
            data, _ = stream.read(block)
            data = data[:, 0].copy()
            level = float(np.sqrt(np.mean(data ** 2)))
            floor = level if floor is None else (0.995 * floor + 0.005 * level if not clip else floor)
            loud = level > max(floor * 3.0, 0.012)
            blocks += 1
            if blocks % 600 == 0 and should_stop():
                return None
            if not clip:
                pre = (pre + [data])[-6:]           # 300 ms pre-roll
                if loud:
                    clip, quiet = pre[:], 0.0
                continue
            clip.append(data)
            quiet = 0.0 if loud else quiet + 0.05
            if quiet >= 0.5 or len(clip) * 0.05 >= max_clip:
                audio = np.concatenate(clip)
                voiced = sum(float(np.sqrt(np.mean(c ** 2))) > max(floor * 3.0, 0.012) for c in clip)
                clip, pre = [], []
                if len(audio) < sr * 0.4 or voiced < 6:   # under ~0.3 s of real sound
                    continue
                # Only the start is needed to spot the wake phrase. The text is checked and
                # discarded here; nothing that isn't a wake phrase is logged or stored.
                text = transcribe(audio[: int(sr * 2.5)], quick=True)
                m = pattern.search(text.lower())
                if not m:
                    continue
                if len(audio) > sr * 2.5:          # wake phrase + command: transcribe it all
                    text = transcribe(audio)
                    m = pattern.search(text.lower()) or m
                print(f"  [wake] heard: {text}")
                rest = text[m.end():]
                while pattern.match(rest.strip(" ,.!?").lower()):     # "Hey Sunny. Hey Sunny."
                    rest = rest.strip(" ,.!?")[pattern.match(rest.strip(" ,.!?").lower()).end():]
                return rest.strip(" ,.!?")
