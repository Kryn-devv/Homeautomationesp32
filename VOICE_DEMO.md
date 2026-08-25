# FRIDAY Smart Home Voice Control — Demo Guide (Windows)

A standalone, fully **local** voice controller for the ESP32 4-channel relay
board. Speech recognition runs on your laptop with **openai-whisper
(`tiny.en`)** — no Google Speech, no cloud AI, no internet needed after the
one-time model download. Command parsing is a deterministic keyword matcher
(no LLM). The program only ever calls a fixed whitelist of ESP32 endpoints
and **never modifies or flashes the ESP32 firmware**.

```
Microphone → audio capture → local Whisper (tiny.en)
           → deterministic parser → fixed HTTP endpoint → ESP32 → relays
```

## Requirements

- Windows PC with a microphone and speaker
- **Python 3.11 or 3.12** (openai-whisper is not reliable on newer
  versions — check with `python --version` first; if you see 3.13/3.14,
  install Python 3.12 from python.org and use `py -3.12` below)
- The ESP32 relay controller already running on your Wi-Fi
- Your PC and the ESP32 on the **same Wi-Fi network**

## 1. Install dependencies

```bat
python -m pip install -r requirements-voice.txt
```

(If you have several Pythons: `py -3.12 -m pip install -r requirements-voice.txt`)

### If PyAudio fails to install

Recent PyAudio releases ship prebuilt Windows wheels for Python 3.11/3.12,
so plain `pip install PyAudio` normally works. If it still tries to compile
and fails, install a prebuilt wheel:

```bat
python -m pip install pipwin
python -m pipwin install pyaudio
```

### ffmpeg is NOT required

Whisper's file loader normally needs ffmpeg, but `friday_voice.py` passes
recorded audio to Whisper as an in-memory numpy array, so you do not need
to install ffmpeg.

## 2. Download the Whisper model (one time, needs internet)

```bat
python -c "import whisper; whisper.load_model('tiny.en')"
```

This caches `tiny.en` (~75 MB) under `%USERPROFILE%\.cache\whisper`. After
this, the demo runs fully offline (except for the local Wi-Fi to the ESP32).

## 3. Set the ESP32 address

If your ESP32 gets a new IP (its address can change between Wi-Fi sessions —
read it from the ESP32 serial monitor or your router), edit **one variable**
at the top of `friday_voice.py`:

```python
ESP32_BASE_URL = "http://10.36.98.43"
```

## 4. Run FRIDAY

```bat
python friday_voice.py
```

You should see:

```
==========================================================
        FRIDAY SMART HOME VOICE CONTROL
==========================================================

ESP32: http://10.36.98.43

Status: Connected
FRIDAY: Home controller connected.
```

Then say a command after `Listening...`. Press `Ctrl+C` to quit.

Timing behavior (the program never hangs):

- waits up to **8 s** for you to start speaking, then loops back
- records at most **6 s** per command
- every HTTP request to the ESP32 times out after **4 s**
- the same command repeated within **2 s** is executed only once
- the Whisper model loads once at startup and is cached in memory

## Supported voice commands

| Say | Effect | Endpoint |
| --- | --- | --- |
| "turn on light" / "light on" / "light chalu karo" / "batti chalu karo" | Light ON | `GET /light/on` |
| "turn off light" / "light off" / "light band karo" / "batti band karo" | Light OFF | `GET /light/off` |
| "turn on fan" / "fan on" / "fan chalu karo" | Fan ON | `GET /fan/on` |
| "turn off fan" / "fan off" / "fan band karo" | Fan OFF | `GET /fan/off` |
| "turn on appliance" / "appliance on" / "appliance chalu karo" | Appliance ON | `GET /appliance/on` |
| "turn off appliance" / "appliance off" / "appliance band karo" | Appliance OFF | `GET /appliance/off` |
| "turn on appliance four" / "relay 4 on" | Relay 4 ON | `GET /relay4/on` |
| "turn off appliance 4" / "relay 4 off" | Relay 4 OFF | `GET /relay4/off` |
| "turn everything off" / "all off" / "sab band karo" / "sab kuch band karo" | Everything OFF | `GET /all/off` |

Anything else gets: *"I didn't understand that command."*
If the ESP32 can't be reached: *"I cannot reach the home controller."*

These nine endpoints are the **only** URLs the program can ever request —
URLs are looked up from a fixed table, never built from your speech.

## Run the offline tests

No microphone, ESP32, internet, or Whisper model needed:

```bat
python -m unittest test.test_friday_voice -v
```

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `Status: Not reachable` | Confirm the ESP32 IP (serial monitor / router page), update `ESP32_BASE_URL`, make sure PC and ESP32 share the same Wi-Fi, and try opening `http://<esp32-ip>/` in a browser. |
| "Could not initialize the microphone" | Plug in / enable a mic, allow microphone access in Windows Settings → Privacy, reinstall PyAudio (see above). |
| Recognition is poor in a noisy room | Sit closer to the mic; the program recalibrates ambient noise only at startup, so restart it if the room noise changes a lot. |
| No spoken replies | pyttsx3 uses Windows SAPI5 voices; check your default speaker. The demo still works — replies are always printed too. |
| First command feels slow | The model loads at startup; if you skipped step 2, the first run also downloads it. |
