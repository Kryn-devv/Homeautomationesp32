"""
FRIDAY - Voice-controlled smart home demo for Windows.

Microphone -> local Whisper (tiny.en) -> deterministic parser -> fixed
whitelisted HTTP endpoints on the ESP32 relay controller.

Everything runs locally: no cloud speech API, no LLM parsing.
The ESP32 firmware is never modified by this program.

Heavy dependencies (whisper, speech_recognition/PyAudio, pyttsx3, numpy)
are imported lazily inside functions so that importing this module is cheap,
works in offline unit tests, and never downloads the Whisper model.
"""

import sys
import time

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# The ONE variable to change if the ESP32 gets a new IP address on Wi-Fi.
ESP32_BASE_URL = "http://10.36.98.43"

WHISPER_MODEL_NAME = "tiny.en"   # small + fast; English commands only

HTTP_TIMEOUT_SECONDS = 4.0       # per request to the ESP32
LISTEN_TIMEOUT_SECONDS = 8.0     # max wait for speech to START
MAX_PHRASE_SECONDS = 6.0         # max RECORDING length once speech starts
DUPLICATE_WINDOW_SECONDS = 2.0   # suppress repeats of the same command

# ---------------------------------------------------------------------------
# Fixed endpoint whitelist. URLs are ONLY ever built from this table --
# never from recognized speech text.
# ---------------------------------------------------------------------------

ENDPOINTS = {
    "light_on":      "/light/on",
    "light_off":     "/light/off",
    "fan_on":        "/fan/on",
    "fan_off":       "/fan/off",
    "appliance_on":  "/appliance/on",
    "appliance_off": "/appliance/off",
    "relay4_on":     "/relay4/on",
    "relay4_off":    "/relay4/off",
    "all_off":       "/all/off",
}

# What FRIDAY says before sending / after success, per command.
PHRASES = {
    "light_on":      ("Turning on the light.",      "Light turned on."),
    "light_off":     ("Turning off the light.",     "Light turned off."),
    "fan_on":        ("Turning on the fan.",        "Fan turned on."),
    "fan_off":       ("Turning off the fan.",       "Fan turned off."),
    "appliance_on":  ("Turning on the appliance.",  "Appliance turned on."),
    "appliance_off": ("Turning off the appliance.", "Appliance turned off."),
    "relay4_on":     ("Turning on relay four.",     "Relay four turned on."),
    "relay4_off":    ("Turning off relay four.",    "Relay four turned off."),
    "all_off":       ("Turning everything off.",    "Everything is off."),
}

UNKNOWN_COMMAND_REPLY = "I didn't understand that command."
UNREACHABLE_REPLY = "I cannot reach the home controller."

# ---------------------------------------------------------------------------
# Deterministic command parser (no LLM)
# ---------------------------------------------------------------------------

_ON_WORDS = {"on", "chalu", "chaalu", "shuru", "start"}
_OFF_WORDS = {"off", "band", "bandh", "stop"}
_ALL_WORDS = {"all", "everything", "sab", "sabkuch"}
_LIGHT_WORDS = {"light", "lights", "batti", "bati"}
_FAN_WORDS = {"fan", "pankha"}


def normalize_text(text):
    """Lowercase, strip punctuation, collapse whitespace, 'four' -> '4'."""
    cleaned = []
    for ch in text.lower():
        cleaned.append(ch if (ch.isalnum() or ch.isspace()) else " ")
    tokens = "".join(cleaned).split()
    tokens = ["4" if t == "four" else t for t in tokens]
    return " ".join(tokens)


def parse_command(text):
    """Map recognized speech to a whitelisted command key, or None.

    Purely deterministic keyword matching over normalized tokens.
    Supports English and simple Hinglish (chalu/band, batti, sab...).
    """
    if not text:
        return None

    joined = normalize_text(text)
    tokens = set(joined.split())
    if not tokens:
        return None

    wants_on = bool(tokens & _ON_WORDS)
    wants_off = bool(tokens & _OFF_WORDS)
    if wants_on == wants_off:
        # Neither, or contradictory ("turn on and off") -> reject.
        return None

    # "all off" family first (sab kuch band karo, turn everything off, ...)
    if wants_off and (tokens & _ALL_WORDS):
        return "all_off"

    # Relay 4: "relay 4", "relay4", "appliance 4" (after four->4 mapping)
    if "relay 4" in joined or "relay4" in tokens or "appliance 4" in joined:
        device = "relay4"
    elif "appliance" in tokens:
        device = "appliance"
    elif tokens & _FAN_WORDS:
        device = "fan"
    elif tokens & _LIGHT_WORDS:
        device = "light"
    else:
        return None

    return f"{device}_{'on' if wants_on else 'off'}"


# ---------------------------------------------------------------------------
# Duplicate protection
# ---------------------------------------------------------------------------

_last_command = None
_last_command_time = 0.0


def reset_duplicate_guard():
    global _last_command, _last_command_time
    _last_command = None
    _last_command_time = 0.0


def is_duplicate(command, now=None):
    """True if `command` repeats the previous one within the window."""
    global _last_command, _last_command_time
    if now is None:
        now = time.monotonic()
    if command == _last_command and (now - _last_command_time) < DUPLICATE_WINDOW_SECONDS:
        return True
    _last_command = command
    _last_command_time = now
    return False


# ---------------------------------------------------------------------------
# ESP32 HTTP layer (whitelist only, bounded timeout, no stack traces)
# ---------------------------------------------------------------------------

def endpoint_for(command):
    """Return the whitelisted path for a command key, or None."""
    return ENDPOINTS.get(command)


def send_command(command):
    """Send one whitelisted command to the ESP32.

    Returns (ok, error_message). Never raises, never builds a URL from
    anything except the fixed ENDPOINTS table.
    """
    path = endpoint_for(command)
    if path is None:
        return False, "Command not in whitelist."
    url = ESP32_BASE_URL.rstrip("/") + path
    try:
        response = requests.get(url, timeout=HTTP_TIMEOUT_SECONDS)
        if response.status_code == 200:
            return True, None
        return False, f"ESP32 returned HTTP {response.status_code}."
    except requests.exceptions.RequestException as exc:
        return False, f"Connection problem: {exc.__class__.__name__}"


def check_esp32():
    """Startup connectivity check. Returns True if the ESP32 responds."""
    try:
        response = requests.get(
            ESP32_BASE_URL.rstrip("/") + "/status",
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        return response.status_code == 200
    except requests.exceptions.RequestException:
        return False


# ---------------------------------------------------------------------------
# Text-to-speech (pyttsx3, local, Windows SAPI5)
# ---------------------------------------------------------------------------

def speak(text):
    """Speak locally through the laptop speaker. Falls back to print-only.

    A fresh engine per call is slightly slower but avoids the well-known
    pyttsx3 issue where a reused engine goes silent inside long loops.
    """
    print(f"FRIDAY: {text}")
    try:
        import pyttsx3
        engine = pyttsx3.init()
        engine.setProperty("rate", 175)
        engine.say(text)
        engine.runAndWait()
        engine.stop()
    except Exception as exc:
        print(f"[warn] Text-to-speech unavailable: {exc.__class__.__name__}")


# ---------------------------------------------------------------------------
# Whisper (local, lazy-loaded, cached)
# ---------------------------------------------------------------------------

_whisper_model = None


def get_whisper_model():
    """Load the Whisper model once and cache it for all later commands."""
    global _whisper_model
    if _whisper_model is None:
        import whisper
        print(f"Loading local Whisper model '{WHISPER_MODEL_NAME}' "
              "(first run downloads it, then it is cached)...")
        _whisper_model = whisper.load_model(WHISPER_MODEL_NAME)
        print("Whisper model ready.")
    return _whisper_model


def transcribe_audio(audio_data):
    """Transcribe one short recorded clip with local Whisper.

    `audio_data` is a speech_recognition.AudioData. The raw PCM is converted
    to a 16 kHz float32 numpy array and handed to Whisper directly, so no
    ffmpeg install and no temp files are needed.

    Synchronous on purpose: the clip is bounded to MAX_PHRASE_SECONDS, so
    tiny.en finishes in a couple of seconds. Returns the text, or None on
    any failure (never raises, never crashes the loop).
    """
    try:
        model = get_whisper_model()
        raw = audio_data.get_raw_data(convert_rate=16000, convert_width=2)
        import numpy as np
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        result = model.transcribe(samples, language="en", fp16=False)
        return (result.get("text") or "").strip()
    except Exception as exc:
        print(f"[error] Whisper transcription failed: {exc.__class__.__name__}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Microphone capture (bounded, never hangs)
# ---------------------------------------------------------------------------

def init_microphone():
    """Create (recognizer, microphone) or (None, None) on failure."""
    try:
        import speech_recognition as sr
        recognizer = sr.Recognizer()
        recognizer.pause_threshold = 0.6
        recognizer.dynamic_energy_threshold = True
        microphone = sr.Microphone()
        with microphone as source:
            print("Calibrating microphone for ambient noise (1 second)...")
            recognizer.adjust_for_ambient_noise(source, duration=1.0)
        print("Microphone ready.")
        return recognizer, microphone
    except Exception as exc:
        print(f"[error] Could not initialize the microphone: "
              f"{exc.__class__.__name__}: {exc}")
        print("        Check that a microphone is connected and that "
              "PyAudio is installed (see VOICE_DEMO.md).")
        return None, None


def listen_once(recognizer, microphone):
    """Record one short command. Returns AudioData or None on timeout.

    Bounded on both ends: waits at most LISTEN_TIMEOUT_SECONDS for speech
    to start, and records at most MAX_PHRASE_SECONDS once it does.
    """
    import speech_recognition as sr
    try:
        with microphone as source:
            print("\nListening...")
            return recognizer.listen(
                source,
                timeout=LISTEN_TIMEOUT_SECONDS,
                phrase_time_limit=MAX_PHRASE_SECONDS,
            )
    except sr.WaitTimeoutError:
        return None
    except Exception as exc:
        print(f"[error] Microphone capture failed: {exc.__class__.__name__}")
        return None


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def handle_text(text):
    """Parse recognized text and execute it. Returns the command key or None."""
    command = parse_command(text)
    if command is None:
        speak(UNKNOWN_COMMAND_REPLY)
        return None

    if is_duplicate(command):
        print(f"[info] Ignoring duplicate command '{command}' "
              f"(within {DUPLICATE_WINDOW_SECONDS:.0f}s).")
        return command

    intent_phrase, success_phrase = PHRASES[command]
    speak(intent_phrase)

    ok, error = send_command(command)
    if ok:
        speak(success_phrase)
    else:
        print(f"[error] {error}")
        speak(UNREACHABLE_REPLY)
    return command


def main():
    print("=" * 58)
    print("        FRIDAY SMART HOME VOICE CONTROL")
    print("=" * 58)
    print()
    print(f"ESP32: {ESP32_BASE_URL}")
    print()

    if check_esp32():
        print("Status: Connected")
        speak("Home controller connected.")
    else:
        print("Status: Not reachable")
        print("        Commands will still be attempted; check the ESP32 IP")
        print("        in ESP32_BASE_URL at the top of friday_voice.py.")

    recognizer, microphone = init_microphone()
    if recognizer is None:
        print("Cannot continue without a microphone. Exiting.")
        return 1

    # Load Whisper up front so the first voice command is not slow.
    try:
        get_whisper_model()
    except Exception as exc:
        print(f"[error] Could not load the Whisper model: "
              f"{exc.__class__.__name__}: {exc}")
        print("        Run: pip install -r requirements-voice.txt")
        return 1

    speak("Friday is ready.")
    print("Say a command, e.g. 'turn on light', 'fan band karo', 'all off'.")
    print("Press Ctrl+C to quit.")

    while True:
        try:
            audio = listen_once(recognizer, microphone)
            if audio is None:
                continue

            print("Processing...")
            text = transcribe_audio(audio)
            if text is None:
                # Whisper failed; error already printed. Keep the loop alive.
                continue
            if not text:
                print("(heard nothing intelligible)")
                continue

            print(f"Recognized: \"{text}\"")
            handle_text(text)
        except KeyboardInterrupt:
            print("\nShutting down.")
            speak("Goodbye.")
            return 0
        except Exception as exc:
            # Last-resort guard: never crash out of the demo loop.
            print(f"[error] Unexpected problem: {exc.__class__.__name__}: {exc}")
            continue


if __name__ == "__main__":
    sys.exit(main())
