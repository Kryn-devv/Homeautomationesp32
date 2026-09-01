"""
FRIDAY Chat - local chatbot with a web UI and OLED-eyes emotions.

Runs a small Flask server on http://localhost:5000 with a chat page.
Replies come from a deterministic rule engine (no cloud AI, no LLM).
Each reply carries an emotion; the emotion is mirrored on the page's
avatar AND sent to the NOVA EYES ESP32-S3 (nova_eyes/main.cpp) so the
physical eyes animate along with the conversation.

Smart-home commands typed into the chat ("turn on light", "sab band
karo", ...) are parsed and executed through the relay ESP32 using the
same whitelist as friday_voice.py.

Run:  py -3.12 friday_chat.py   then open http://localhost:5000
"""

import datetime
import random
import time

import requests
from flask import Flask, jsonify, render_template_string, request

import friday_voice as fv

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# The NOVA EYES ESP32-S3 (see nova_eyes/main.cpp). Its IP prints on the
# serial monitor and on the OLED at boot. CHANGE THIS when the IP changes.
EYES_ESP32_URL = "http://192.168.1.35"

# The relay controller is taken from friday_voice.py (ESP32_BASE_URL).

EYES_TIMEOUT_SECONDS = 2.0

EMOTIONS = {"neutral", "happy", "sad", "angry", "surprised", "sleepy",
            "thinking"}
EMOJI = {
    "neutral": "🙂", "happy": "😄", "sad": "😢", "angry": "😠",
    "surprised": "😲", "sleepy": "😴", "thinking": "🤔",
}

# ---------------------------------------------------------------------------
# Eyes control (best effort -- chat still works if the eyes are offline)
# ---------------------------------------------------------------------------

def send_emotion(emotion):
    """Push an emotion to the NOVA EYES display. Never raises."""
    if emotion not in EMOTIONS:
        return False
    try:
        requests.get(f"{EYES_ESP32_URL.rstrip('/')}/emotion/{emotion}",
                     timeout=EYES_TIMEOUT_SECONDS)
        return True
    except requests.exceptions.RequestException:
        return False


# ---------------------------------------------------------------------------
# Deterministic reply engine (no LLM)
# ---------------------------------------------------------------------------

RULES = [
    # (keywords - any match, replies - random pick, emotion)
    (["hello", "hii", "hi ", "hey", "namaste", "hola"],
     ["Hello! I am FRIDAY, your home assistant. Ask me something or tell "
      "me to control the lights!",
      "Hey there! FRIDAY at your service.",
      "Namaste! How can I help you today?"],
     "happy"),
    (["who are you", "your name", "what are you"],
     ["I'm FRIDAY - a fully local smart home assistant. I control the "
      "relays, listen to voice commands, and my eyes live on a little "
      "OLED screen!"],
     "happy"),
    (["how are you", "kaise ho", "kya haal"],
     ["Running at full power! All systems nominal.",
      "I'm great - my relays are clicking and my eyes are blinking."],
     "happy"),
    (["joke", "funny", "laugh"],
     ["Why did the ESP32 go to therapy? Too many unresolved interrupts!",
      "I would tell you a UDP joke... but you might not get it.",
      "My favourite kind of music? Heavy metal. Relays love it."],
     "happy"),
    (["time", "samay", "baje"],
     [], "neutral"),  # dynamic, handled below
    (["date", "din", "today"],
     [], "neutral"),  # dynamic, handled below
    (["thank", "dhanyavad", "shukriya"],
     ["Anytime! That's what I'm here for.",
      "You're welcome!"],
     "happy"),
    (["bye", "goodbye", "good night", "sleep", "so jao"],
     ["Goodbye! Waking me is just one message away.",
      "Good night! Powering down the excitement..."],
     "sleepy"),
    (["sad", "cry", "dukhi"],
     ["Oh no, I'm sorry to hear that. Want me to tell you a joke?"],
     "sad"),
    (["angry", "gussa", "hate"],
     ["Whoa! Deep breaths. Should I dim the lights for a calmer mood?"],
     "angry"),
    (["wow", "amazing", "incredible", "awesome"],
     ["I know, right?!", "Impressive stuff!"],
     "surprised"),
    (["who made you", "creator", "banaya"],
     ["I was built as a school smart-home project - Python on the laptop, "
      "two ESP32 boards, and a lot of experimentation!"],
     "happy"),
    (["what can you do", "help", "commands"],
     ["I can chat, tell jokes, give the time, and control the home: try "
      "'turn on light', 'fan off', 'sab band karo', or 'all off'. My eyes "
      "also show how I feel!"],
     "happy"),
]

RELAY_REPLIES_OK = {
    "light_on": "Done! The light is on.",
    "light_off": "Light switched off.",
    "fan_on": "Fan is spinning up!",
    "fan_off": "Fan stopped.",
    "appliance_on": "Appliance powered on.",
    "appliance_off": "Appliance powered off.",
    "relay4_on": "Relay four is on.",
    "relay4_off": "Relay four is off.",
    "all_off": "Everything is off. Silence at last!",
}


def make_reply(message):
    """Deterministic reply for one chat message.

    Returns (reply_text, emotion). Home-control commands are executed on
    the relay ESP32 first; otherwise keyword rules apply.
    """
    text = (message or "").strip()
    if not text:
        return "Say something and I'll answer!", "neutral"
    lower = " " + text.lower() + " "

    # 1) Smart-home command? Reuse friday_voice's parser + whitelist.
    command = fv.parse_command(text)
    if command is not None:
        ok, error = fv.send_command(command)
        if ok:
            return RELAY_REPLIES_OK[command], "happy"
        return ("I couldn't reach the home controller, so that switch "
                "didn't change. Check the relay ESP32 and its IP."), "sad"

    # 2) Dynamic answers.
    if any(k in lower for k in ("time", "samay", "baje")):
        now = datetime.datetime.now().strftime("%I:%M %p")
        return f"It's {now} right now.", "neutral"
    if any(k in lower for k in ("date", " din ", "today")):
        today = datetime.datetime.now().strftime("%A, %d %B %Y")
        return f"Today is {today}.", "neutral"

    # 3) Keyword rules.
    for keywords, replies, emotion in RULES:
        if replies and any(k in lower for k in keywords):
            return random.choice(replies), emotion

    # 4) Fallback.
    return ("Hmm, I don't know that one yet. I'm a rule-based bot - try "
            "'help' to see what I understand."), "thinking"


# ---------------------------------------------------------------------------
# Web app
# ---------------------------------------------------------------------------

app = Flask(__name__)

PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>FRIDAY Chat</title>
<style>
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; font-family: 'Segoe UI', Arial, sans-serif;
    background: linear-gradient(160deg, #0b1220 0%, #12203a 100%);
    color: #e8eefb; display: flex; flex-direction: column; align-items: center;
  }
  .shell { width: min(680px, 100%); padding: 18px; display: flex;
           flex-direction: column; height: 100vh; }
  header { text-align: center; padding-bottom: 10px; }
  h1 { margin: 0; font-size: 22px; letter-spacing: 3px; color: #7dd3fc; }
  .sub { color: #7c8db0; font-size: 12px; margin-top: 4px; }
  #avatar { font-size: 64px; text-align: center; line-height: 1.1;
            transition: transform .15s ease; user-select: none; }
  #avatar.pop { transform: scale(1.18); }
  #emotion-label { text-align: center; color: #7c8db0; font-size: 12px;
                   text-transform: uppercase; letter-spacing: 2px; }
  #chat { flex: 1; overflow-y: auto; margin: 12px 0; padding: 10px;
          background: rgba(255,255,255,.04); border-radius: 12px; }
  .msg { max-width: 78%; margin: 6px 0; padding: 10px 14px;
         border-radius: 14px; line-height: 1.35; font-size: 15px;
         white-space: pre-wrap; word-wrap: break-word; }
  .user { background: #2563eb; margin-left: auto; border-bottom-right-radius: 4px; }
  .bot  { background: #1e293b; margin-right: auto; border-bottom-left-radius: 4px; }
  form { display: flex; gap: 8px; }
  input { flex: 1; padding: 13px 16px; border-radius: 999px; border: 0;
          background: #1e293b; color: #e8eefb; font-size: 15px; outline: none; }
  button { padding: 13px 22px; border-radius: 999px; border: 0;
           background: #06b6d4; color: #05242b; font-weight: 800;
           font-size: 15px; cursor: pointer; }
  button:active { transform: scale(.97); }
</style>
</head>
<body>
<div class="shell">
  <header>
    <h1>F R I D A Y</h1>
    <div class="sub">local chatbot &middot; relay ESP32 + OLED eyes</div>
  </header>
  <div id="avatar">🙂</div>
  <div id="emotion-label">neutral</div>
  <div id="chat"></div>
  <form id="form">
    <input id="input" placeholder="Type a message... e.g. 'turn on light' or 'tell me a joke'" autocomplete="off" autofocus>
    <button>Send</button>
  </form>
</div>
<script>
const chat = document.getElementById('chat');
const form = document.getElementById('form');
const input = document.getElementById('input');
const avatar = document.getElementById('avatar');
const label = document.getElementById('emotion-label');

function add(text, cls) {
  const div = document.createElement('div');
  div.className = 'msg ' + cls;
  div.textContent = text;
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  add(text, 'user');
  input.value = '';
  avatar.textContent = '🤔'; label.textContent = 'thinking';
  try {
    const r = await fetch('/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: text}),
    });
    const data = await r.json();
    add(data.reply, 'bot');
    avatar.textContent = data.emoji;
    label.textContent = data.emotion;
    avatar.classList.add('pop');
    setTimeout(() => avatar.classList.remove('pop'), 180);
  } catch (err) {
    add('(FRIDAY server not reachable)', 'bot');
  }
});

add("Hello! I'm FRIDAY. Chat with me, or type 'help' to see what I can do.", 'bot');
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(PAGE)


@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    message = str(data.get("message", ""))[:500]
    reply, emotion = make_reply(message)
    send_emotion(emotion)  # best effort; ignored if eyes are offline
    return jsonify({"reply": reply, "emotion": emotion,
                    "emoji": EMOJI[emotion]})


def main():
    print("=" * 58)
    print("        FRIDAY CHATBOT (local)")
    print("=" * 58)
    print(f"Relay ESP32: {fv.ESP32_BASE_URL}")
    print(f"Eyes ESP32-S3: {EYES_ESP32_URL}")
    if send_emotion("happy"):
        print("Eyes: connected (they should look happy right now!)")
    else:
        print("Eyes: not reachable - chat still works, emotions shown "
              "on the page only.")
    print()
    print("Open http://localhost:5000 in your browser. Ctrl+C to stop.")
    app.run(host="127.0.0.1", port=5000, debug=False)


if __name__ == "__main__":
    main()
