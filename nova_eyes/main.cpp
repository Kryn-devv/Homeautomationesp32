// NOVA EYES - animated robot eyes with expressions for ESP32-S3 + SSD1306.
//
// Copy this file over src/main.cpp in your Nova PlatformIO project
// (board: rymcu-esp32-s3-devkitc-1, libs: Adafruit SSD1306 + GFX -- already
// in your platformio.ini).
//
// Wiring (I2C): OLED VCC->3.3V, GND->GND, SDA->GPIO 8, SCL->GPIO 9.
//
// The eyes animate on their own (blinking, glancing around) and expose a
// small HTTP API so the FRIDAY chatbot on your PC can set emotions:
//
//   GET /               -> info page
//   GET /status         -> {"emotion":"happy"}
//   GET /emotion/neutral | happy | sad | angry | surprised | sleepy | thinking
//   GET /blink          -> force a blink
//
// If Wi-Fi is unavailable the eyes still run standalone.

#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <WiFi.h>
#include <WebServer.h>

// ---------------------------------------------------------------- config --
const char* ssid = "APPLE";
const char* password = "3672@Bhagat";

#define SDA_PIN 8
#define SCL_PIN 9
#define OLED_ADDR 0x3C   // try 0x3D if the screen stays black
#define SCREEN_W 128
#define SCREEN_H 64

const unsigned long EMOTION_HOLD_MS = 7000;  // then back to neutral

// ---------------------------------------------------------------- state ---
Adafruit_SSD1306 display(SCREEN_W, SCREEN_H, &Wire, -1);
WebServer server(80);

enum Emotion { NEUTRAL, HAPPY, SAD, ANGRY, SURPRISED, SLEEPY, THINKING };
const char* EMOTION_NAMES[] = {"neutral", "happy", "sad", "angry",
                               "surprised", "sleepy", "thinking"};

Emotion emotion = NEUTRAL;
unsigned long emotionUntil = 0;

// blink animation: 0 = open, 1 = closing, 2 = opening
int blinkStage = 0;
unsigned long blinkStageStart = 0;
unsigned long nextBlinkAt = 2000;
float openAmount = 1.0f;

// wandering gaze
float lookX = 0, lookY = 0;
float lookTX = 0, lookTY = 0;
unsigned long nextLookAt = 3000;

bool wifiOk = false;
unsigned long showIpUntil = 0;

// ------------------------------------------------------------- rendering --
void drawEyes() {
  display.clearDisplay();

  int eyeW = 30, eyeHFull = 36, radius = 10;
  float open = openAmount;

  if (emotion == SURPRISED) { eyeW = 34; eyeHFull = 44; radius = 15; }
  if (emotion == SLEEPY   && open > 0.35f) open = 0.35f;
  if (emotion == THINKING && open > 0.85f) open = 0.85f;

  int eyeH = max(3, (int)(eyeHFull * open));
  int cy = 30 + (int)lookY;

  for (int i = 0; i < 2; i++) {
    int cx = (i == 0 ? 40 : 88) + (int)lookX;
    int x = cx - eyeW / 2;
    int y = cy - eyeH / 2;
    int r = min(radius, eyeH / 2);

    display.fillRoundRect(x, y, eyeW, eyeH, r, SSD1306_WHITE);

    if (emotion == HAPPY) {
      // black circle eats the lower half -> upward crescent "smiling eyes"
      display.fillCircle(cx, y + eyeH + 4, eyeW / 2 + 4, SSD1306_BLACK);
    } else if (emotion == SAD) {
      // droop the OUTER top corner of each eye
      if (i == 0)
        display.fillTriangle(x - 1, y - 1, x + (int)(eyeW * 0.6), y - 1,
                             x - 1, y + (int)(eyeH * 0.55), SSD1306_BLACK);
      else
        display.fillTriangle(x + eyeW, y - 1, x + (int)(eyeW * 0.4), y - 1,
                             x + eyeW, y + (int)(eyeH * 0.55), SSD1306_BLACK);
    } else if (emotion == ANGRY) {
      // slant the INNER top corner of each eye down toward the nose
      if (i == 0)
        display.fillTriangle(x + (int)(eyeW * 0.4), y - 1, x + eyeW + 1, y - 1,
                             x + eyeW + 1, y + (int)(eyeH * 0.55),
                             SSD1306_BLACK);
      else
        display.fillTriangle(x - 1, y - 1, x + (int)(eyeW * 0.6), y - 1,
                             x - 1, y + (int)(eyeH * 0.55), SSD1306_BLACK);
    }
  }

  // small helper text while booting so you can find the IP
  if (millis() < showIpUntil) {
    display.setTextSize(1);
    display.setTextColor(SSD1306_WHITE);
    display.setCursor(0, 56);
    if (wifiOk) {
      display.print("IP: ");
      display.print(WiFi.localIP());
    } else {
      display.print("WiFi: offline mode");
    }
  }

  display.display();
}

// ------------------------------------------------------------- animation --
void updateAnimation() {
  unsigned long now = millis();

  // emotion falls back to neutral after the hold time
  if (emotionUntil != 0 && now > emotionUntil) {
    emotion = NEUTRAL;
    emotionUntil = 0;
  }

  // blinking state machine (closing 70 ms, opening 120 ms)
  if (blinkStage == 0 && now >= nextBlinkAt) {
    blinkStage = 1;
    blinkStageStart = now;
  }
  if (blinkStage == 1) {
    float t = (now - blinkStageStart) / 70.0f;
    openAmount = max(0.0f, 1.0f - t);
    if (t >= 1.0f) { blinkStage = 2; blinkStageStart = now; }
  } else if (blinkStage == 2) {
    float t = (now - blinkStageStart) / 120.0f;
    openAmount = min(1.0f, t);
    if (t >= 1.0f) {
      blinkStage = 0;
      openAmount = 1.0f;
      nextBlinkAt = now + random(2500, 6000);
    }
  }

  // wandering gaze; thinking looks up and to the side
  if (emotion == THINKING) {
    lookTX = 8;
    lookTY = -6;
  } else if (now >= nextLookAt) {
    if (random(0, 2) == 0) { lookTX = 0; lookTY = 0; }
    else { lookTX = random(-8, 9); lookTY = random(-4, 5); }
    nextLookAt = now + random(2000, 5000);
  }
  lookX += (lookTX - lookX) * 0.15f;
  lookY += (lookTY - lookY) * 0.15f;
}

// ------------------------------------------------------------ HTTP layer --
void setEmotion(Emotion e) {
  emotion = e;
  emotionUntil = (e == NEUTRAL) ? 0 : millis() + EMOTION_HOLD_MS;
}

void registerEmotionRoute(const char* path, Emotion e) {
  server.on(path, [e]() {
    setEmotion(e);
    server.send(200, "text/plain", "OK");
  });
}

void setup() {
  Serial.begin(115200);
  randomSeed(esp_random());

  Wire.begin(SDA_PIN, SCL_PIN);
  if (!display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR)) {
    Serial.println("SSD1306 not found at 0x3C - check wiring / try 0x3D");
    while (true) delay(1000);
  }
  display.clearDisplay();
  display.display();

  Serial.println();
  Serial.println("=== NOVA EYES ===");

  WiFi.begin(ssid, password);
  Serial.print("Connecting to WiFi");
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 15000) {
    delay(250);
    Serial.print(".");
  }
  Serial.println();

  wifiOk = (WiFi.status() == WL_CONNECTED);
  if (wifiOk) {
    Serial.print("EYES ESP32-S3 IP: ");
    Serial.println(WiFi.localIP());

    server.on("/", []() {
      String msg = "NOVA EYES\nEmotion: ";
      msg += EMOTION_NAMES[emotion];
      msg += "\nEndpoints: /emotion/<neutral|happy|sad|angry|surprised|"
             "sleepy|thinking>, /blink, /status\n";
      server.send(200, "text/plain", msg);
    });
    server.on("/status", []() {
      String json = "{\"emotion\":\"";
      json += EMOTION_NAMES[emotion];
      json += "\"}";
      server.send(200, "application/json", json);
    });
    registerEmotionRoute("/emotion/neutral", NEUTRAL);
    registerEmotionRoute("/emotion/happy", HAPPY);
    registerEmotionRoute("/emotion/sad", SAD);
    registerEmotionRoute("/emotion/angry", ANGRY);
    registerEmotionRoute("/emotion/surprised", SURPRISED);
    registerEmotionRoute("/emotion/sleepy", SLEEPY);
    registerEmotionRoute("/emotion/thinking", THINKING);
    server.on("/blink", []() {
      blinkStage = 1;
      blinkStageStart = millis();
      server.send(200, "text/plain", "OK");
    });
    server.begin();
    Serial.println("Eyes server started!");
  } else {
    Serial.println("WiFi failed - running eyes standalone.");
  }

  showIpUntil = millis() + 12000;  // show IP on screen for 12 s
  setEmotion(HAPPY);               // greet on boot
}

void loop() {
  if (wifiOk) server.handleClient();

  static unsigned long lastFrame = 0;
  unsigned long now = millis();
  if (now - lastFrame >= 20) {  // ~50 fps
    lastFrame = now;
    updateAnimation();
    drawEyes();
  }
}
