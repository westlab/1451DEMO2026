/*
 * M5Core2 + SCD41  ->  IEEE 1451.1.6 NCAP (MQTT) terminal firmware
 * ------------------------------------------------------------------
 * - Reads Temp / Humidity / CO2 from an M5 SCD41 unit (I2C 0x62, Port.A).
 * - Publishes telemetry over MQTT every TELEMETRY_PERIOD_MS.
 * - Receives a gauge command (0-100) and moves an on-screen bar gauge.
 * - Shows the 4 values + WiFi/MQTT/SCD41 connection status at all times.
 * - Runs a boot self-test (SCD41 / WiFi / MQTT / SUB / PUB) and prints
 *   PASS/FAIL for each step before switching to the normal 4-value view.
 *
 * MQTT contract (must match the host NCAP, do not change):
 *   M5 -> host  m5iot/<id>/telemetry  {"temp":C,"humid":%,"co2":ppm,"gauge":0-100}
 *   host -> M5  m5iot/<id>/gauge      plain number string 0..100
 *   M5 -> host  m5iot/<id>/status     "online"/"offline", retain=true, LWT="offline"
 *
 * Temperature is sent in degrees Celsius (the host converts to Kelvin).
 *
 * Libraries (Arduino-ESP32 core 3.x):
 *   M5Unified 0.2.x, PubSubClient 2.8, ArduinoJson 7.x
 * The SCD41 is driven directly over I2C (Wire) -- no extra library needed.
 */

#include <Wire.h>
#include <M5Unified.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include "secrets.h"   // WIFI_SSID / WIFI_PASS (+ optional MQTT_HOST/PORT)

// ===================== CONFIG (edit DEVICE_ID per board) =====================
#ifndef MQTT_HOST
#define MQTT_HOST           "10.42.0.1"   // LAN broker (offline demo); must match config.yml mqtthost. Override in secrets.h if needed.
#endif
#ifndef MQTT_PORT
#define MQTT_PORT           1883
#endif
#define TOPIC_PREFIX        "m5iot/"
#define DEVICE_ID           "m5-02"               // 2nd board: change to "m5-02"
#define TELEMETRY_PERIOD_MS 2000
// ===========================================================================

// ---- Derived topics --------------------------------------------------------
static const String TOPIC_TELEMETRY = String(TOPIC_PREFIX) + DEVICE_ID + "/telemetry";
static const String TOPIC_GAUGE     = String(TOPIC_PREFIX) + DEVICE_ID + "/gauge";
static const String TOPIC_STATUS    = String(TOPIC_PREFIX) + DEVICE_ID + "/status";
static const String MQTT_CLIENT_ID  = String(DEVICE_ID) + "-core2";

// ---- SCD41 (Sensirion) over I2C -------------------------------------------
#define SCD41_ADDR  0x62
#define SCD41_SDA   32   // Core2 Port.A
#define SCD41_SCL   33

// ---- Globals ---------------------------------------------------------------
WiFiClient   wifiClient;
PubSubClient mqtt(wifiClient);

float  g_temp  = NAN;   // last valid temperature (C)
float  g_humid = NAN;   // last valid humidity (%RH)
int    g_co2   = -1;    // last valid CO2 (ppm)
float  g_gauge = 0.0f;  // current gauge value shown on screen (0..100)
bool   g_haveMeasurement = false;

bool   g_scdOk   = false;
bool   g_wifiOk  = false;
bool   g_mqttOk  = false;

uint32_t g_lastTelemetry = 0;
uint32_t g_lastScdPoll   = 0;
bool   g_gaugeDirty = true;   // force gauge redraw

// Colors
#define COL_BG     0x0000      // black
#define COL_OK     0x07E0      // green
#define COL_FAIL   0xF800      // red
#define COL_WARN   0xFFE0      // yellow
#define COL_TEXT   0xFFFF      // white
#define COL_DIM    0x8410      // gray

// ============================ SCD41 driver ==================================
static uint8_t scd41Crc(const uint8_t *data, uint8_t len) {
  uint8_t crc = 0xFF;
  for (uint8_t i = 0; i < len; i++) {
    crc ^= data[i];
    for (uint8_t b = 0; b < 8; b++) {
      crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x31) : (uint8_t)(crc << 1);
    }
  }
  return crc;
}

static bool scd41SendCmd(uint16_t cmd) {
  Wire.beginTransmission(SCD41_ADDR);
  Wire.write((uint8_t)(cmd >> 8));
  Wire.write((uint8_t)(cmd & 0xFF));
  return Wire.endTransmission() == 0;
}

// Read `words` 16-bit words (each followed by a CRC byte) after a command.
static bool scd41ReadWords(uint16_t cmd, uint16_t *out, uint8_t words, uint16_t delayMs) {
  if (!scd41SendCmd(cmd)) return false;
  if (delayMs) delay(delayMs);
  uint8_t bytes = words * 3;
  uint8_t got = Wire.requestFrom(SCD41_ADDR, (int)bytes);
  if (got != bytes) return false;
  for (uint8_t i = 0; i < words; i++) {
    uint8_t b0 = Wire.read();
    uint8_t b1 = Wire.read();
    uint8_t crc = Wire.read();
    uint8_t buf[2] = { b0, b1 };
    if (scd41Crc(buf, 2) != crc) return false;
    out[i] = ((uint16_t)b0 << 8) | b1;
  }
  return true;
}

static bool scd41GetSerial(uint64_t *serial) {
  uint16_t w[3];
  if (!scd41ReadWords(0x3682, w, 3, 1)) return false;
  *serial = ((uint64_t)w[0] << 32) | ((uint64_t)w[1] << 16) | w[2];
  return true;
}

static bool scd41DataReady() {
  uint16_t w;
  if (!scd41ReadWords(0xE4B8, &w, 1, 1)) return false;
  return (w & 0x07FF) != 0;
}

static bool scd41ReadMeasurement(float *temp, float *humid, int *co2) {
  uint16_t w[3];
  if (!scd41ReadWords(0xEC05, w, 3, 1)) return false;
  *co2   = (int)w[0];
  *temp  = -45.0f + 175.0f * (float)w[1] / 65535.0f;
  *humid = 100.0f * (float)w[2] / 65535.0f;
  return true;
}

// Probe + (re)start periodic measurement. Returns true on success.
static bool scd41Begin() {
  Wire.begin(SCD41_SDA, SCD41_SCL, 100000);
  scd41SendCmd(0x3F86);   // stop_periodic_measurement (in case it was running)
  delay(500);
  uint64_t serial = 0;
  if (!scd41GetSerial(&serial)) return false;
  if (!scd41SendCmd(0x21B1)) return false;   // start_periodic_measurement
  return true;
}

// =============================== Display ====================================
int g_bootY = 0;

void bootLine(const String &msg, uint16_t color) {
  Serial.println(msg);
  M5.Display.setTextColor(color, COL_BG);
  M5.Display.setCursor(8, g_bootY);
  M5.Display.print(msg);
  g_bootY += 22;
}

void bootScreenInit() {
  M5.Display.fillScreen(COL_BG);
  M5.Display.setTextSize(2);
  M5.Display.setTextColor(COL_TEXT, COL_BG);
  M5.Display.setCursor(8, 4);
  M5.Display.print("Boot self-test  ");
  M5.Display.setTextColor(COL_WARN, COL_BG);
  M5.Display.print(DEVICE_ID);
  g_bootY = 36;
}

// ---- Normal 4-value UI -----------------------------------------------------
void drawStatusBar() {
  // top bar y 0..28
  M5.Display.fillRect(0, 0, 320, 30, 0x10A2);
  M5.Display.setTextSize(1);
  M5.Display.setTextDatum(textdatum_t::middle_left);

  M5.Display.setTextColor(g_wifiOk ? COL_OK : COL_FAIL, 0x10A2);
  M5.Display.drawString(g_wifiOk ? "WiFi OK" : "WiFi X", 6, 9);
  M5.Display.setTextColor(g_mqttOk ? COL_OK : COL_FAIL, 0x10A2);
  M5.Display.drawString(g_mqttOk ? "MQTT OK" : "MQTT ^", 92, 9);
  M5.Display.setTextColor(g_scdOk ? COL_OK : COL_FAIL, 0x10A2);
  M5.Display.drawString(g_scdOk ? "SCD41 OK" : "SCD41 X", 188, 9);

  M5.Display.setTextColor(COL_DIM, 0x10A2);
  M5.Display.setTextDatum(textdatum_t::middle_left);
  String ip = g_wifiOk ? WiFi.localIP().toString() : String("--");
  M5.Display.drawString(String(DEVICE_ID) + "  " + WiFi.SSID() + "  " + ip, 6, 23);
  M5.Display.setTextDatum(textdatum_t::top_left);
}

void drawMainStatic() {
  M5.Display.fillScreen(COL_BG);
  drawStatusBar();
  // Labels
  M5.Display.setTextSize(1);
  M5.Display.setTextColor(COL_DIM, COL_BG);
  M5.Display.drawString("TEMP", 14, 40);
  M5.Display.drawString("HUMID", 170, 40);
  M5.Display.drawString("CO2", 14, 110);
  M5.Display.drawString("GAUGE", 14, 170);
  g_gaugeDirty = true;
}

void drawValueField(int x, int y, int w, const String &val, uint16_t color, uint8_t size) {
  M5.Display.fillRect(x, y, w, size * 8 + 4, COL_BG);
  M5.Display.setTextSize(size);
  M5.Display.setTextColor(color, COL_BG);
  M5.Display.setTextDatum(textdatum_t::top_left);
  M5.Display.drawString(val, x, y);
}

void drawMeasurements() {
  // Temp
  String t = (isnan(g_temp)) ? "--.-" : String(g_temp, 1);
  drawValueField(14, 56, 150, t + " C", COL_TEXT, 3);
  // Humid
  String h = (isnan(g_humid)) ? "--.-" : String(g_humid, 1);
  drawValueField(170, 56, 150, h + " %", COL_TEXT, 3);
  // CO2 with color
  uint16_t co2col = COL_OK;
  if (g_co2 > 2000) co2col = COL_FAIL;
  else if (g_co2 > 1000) co2col = COL_WARN;
  String c = (g_co2 < 0) ? "----" : String(g_co2);
  drawValueField(14, 126, 250, c + " ppm", co2col, 3);
}

void drawGauge() {
  // numeric value sits on the label line (above the bar, no overlap)
  drawValueField(200, 162, 110, String((int)(g_gauge + 0.5f)), COL_TEXT, 3);
  // bar below, with a gap so its border is never erased by the value box
  const int gx = 14, gy = 200, gw = 292, gh = 30;
  M5.Display.drawRect(gx, gy, gw, gh, COL_DIM);
  int fillW = (int)((gw - 4) * (g_gauge / 100.0f));
  if (fillW < 0) fillW = 0;
  if (fillW > gw - 4) fillW = gw - 4;
  // clear interior then fill
  M5.Display.fillRect(gx + 2, gy + 2, gw - 4, gh - 4, COL_BG);
  uint16_t bcol = (g_gauge >= 66) ? COL_FAIL : (g_gauge >= 33 ? COL_WARN : COL_OK);
  M5.Display.fillRect(gx + 2, gy + 2, fillW, gh - 4, bcol);
  g_gaugeDirty = false;
}

// ============================== MQTT ========================================
void onMqttMessage(char *topic, byte *payload, unsigned int len) {
  if (String(topic) != TOPIC_GAUGE) return;
  char buf[16];
  unsigned int n = (len < sizeof(buf) - 1) ? len : sizeof(buf) - 1;
  memcpy(buf, payload, n);
  buf[n] = '\0';
  float v = atof(buf);
  if (v < 0) v = 0;
  if (v > 100) v = 100;
  g_gauge = v;
  g_gaugeDirty = true;   // redraw in loop()
  Serial.printf("[RX] gauge = %.1f\n", v);
}

void publishTelemetry() {
  if (!mqtt.connected()) return;
  JsonDocument doc;
  doc["temp"]  = isnan(g_temp)  ? 0.0f : roundf(g_temp * 10) / 10.0f;
  doc["humid"] = isnan(g_humid) ? 0.0f : roundf(g_humid * 10) / 10.0f;
  doc["co2"]   = (g_co2 < 0) ? 0 : g_co2;
  doc["gauge"] = roundf(g_gauge * 10) / 10.0f;
  char out[160];
  size_t n = serializeJson(doc, out, sizeof(out));
  bool ok = mqtt.publish(TOPIC_TELEMETRY.c_str(), (const uint8_t *)out, n, false);
  Serial.printf("[TX] %s rc=%d %s\n", TOPIC_TELEMETRY.c_str(), ok ? 0 : 1, out);
}

// Connect (or reconnect) to MQTT with LWT. Returns true on success.
bool mqttConnect() {
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onMqttMessage);
  mqtt.setBufferSize(512);
  bool ok = mqtt.connect(MQTT_CLIENT_ID.c_str(),
                         nullptr, nullptr,                 // no auth
                         TOPIC_STATUS.c_str(), 0, true,    // LWT topic, qos0, retain
                         "offline");                       // LWT payload
  if (!ok) return false;
  mqtt.publish(TOPIC_STATUS.c_str(), (const uint8_t *)"online", 6, true);  // retain
  mqtt.subscribe(TOPIC_GAUGE.c_str());
  return true;
}

// ============================ Boot sequence =================================
void doSelfTest() {
  bootScreenInit();
  M5.Display.setTextSize(2);

  // 1) SCD41
  bootLine("1) SCD41 detect ...", COL_DIM);
  int tries = 0;
  while (!(g_scdOk = scd41Begin())) {
    g_bootY -= 22;
    bootLine("1) SCD41: FAIL (addr/wiring) retry " + String(++tries), COL_FAIL);
    g_bootY -= 22;
    delay(1000);
  }
  g_bootY -= 22;
  bootLine("1) SCD41: PASS (measuring)        ", COL_OK);

  // 2) WiFi
  bootLine("2) WiFi connect ...", COL_DIM);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED) {
    M5.update();
    if (millis() - t0 > 15000) {           // timeout -> retry
      WiFi.disconnect();
      WiFi.begin(WIFI_SSID, WIFI_PASS);
      t0 = millis();
      g_bootY -= 22;
      bootLine("2) WiFi: retrying ...        ", COL_WARN);
      g_bootY -= 22;
    }
    delay(200);
  }
  g_wifiOk = true;
  g_bootY -= 22;
  bootLine("2) WiFi: PASS (" + WiFi.localIP().toString() + ")        ", COL_OK);

  // 3) MQTT
  bootLine("3) MQTT connect ...", COL_DIM);
  tries = 0;
  while (!mqttConnect()) {
    g_bootY -= 22;
    bootLine("3) MQTT: retry " + String(++tries) + " (rc=" + String(mqtt.state()) + ")  ", COL_WARN);
    g_bootY -= 22;
    delay(1500);
  }
  g_mqttOk = true;
  g_bootY -= 22;
  bootLine("3) MQTT: PASS                  ", COL_OK);

  // 4) Subscribe (done inside mqttConnect, confirm we are still connected)
  bootLine("4) SUB m5iot/" DEVICE_ID "/gauge ...", COL_DIM);
  mqtt.loop();
  bootLine(mqtt.connected() ? "4) SUB: PASS" : "4) SUB: FAIL",
           mqtt.connected() ? COL_OK : COL_FAIL);

  // 5) Publish round-trip: status online + one telemetry, rc must be ok
  bootLine("5) PUB status/telemetry ...", COL_DIM);
  bool pub1 = mqtt.publish(TOPIC_STATUS.c_str(), (const uint8_t *)"online", 6, true);
  publishTelemetry();
  bool pub2 = mqtt.connected();
  bootLine((pub1 && pub2) ? "5) PUB: PASS (rc=0)" : "5) PUB: FAIL",
           (pub1 && pub2) ? COL_OK : COL_FAIL);

  delay(1200);
}

// =============================== Arduino ====================================
void setup() {
  auto cfg = M5.config();
  M5.begin(cfg);
  Serial.begin(115200);
  Serial.println("\n=== M5Core2 " DEVICE_ID " boot ===");
  M5.Display.setRotation(1);
  M5.Display.setTextSize(2);
  M5.Display.setFont(&fonts::Font0);

  doSelfTest();
  drawMainStatic();
  drawMeasurements();
  drawGauge();

  g_lastTelemetry = millis();
  g_lastScdPoll   = millis();
}

void loop() {
  M5.update();

  // ---- keep MQTT alive ----
  if (!mqtt.connected()) {
    g_mqttOk = false;
    drawStatusBar();
    static uint32_t lastTry = 0;
    if (millis() - lastTry > 2000) {     // backoff between attempts
      lastTry = millis();
      if (mqttConnect()) {
        g_mqttOk = true;
        drawStatusBar();
      }
    }
  } else {
    mqtt.loop();
  }

  // ---- WiFi watchdog ----
  bool w = (WiFi.status() == WL_CONNECTED);
  if (w != g_wifiOk) { g_wifiOk = w; drawStatusBar(); }

  // ---- poll SCD41 (~1 Hz; sensor updates ~every 5 s) ----
  if (millis() - g_lastScdPoll > 1000) {
    g_lastScdPoll = millis();
    if (scd41DataReady()) {
      float t, h; int c;
      if (scd41ReadMeasurement(&t, &h, &c)) {
        g_temp = t; g_humid = h; g_co2 = c;
        g_haveMeasurement = true;
        drawMeasurements();
      }
    }
  }

  // ---- redraw gauge if command arrived ----
  if (g_gaugeDirty) drawGauge();

  // ---- telemetry every TELEMETRY_PERIOD_MS ----
  if (millis() - g_lastTelemetry >= TELEMETRY_PERIOD_MS) {
    g_lastTelemetry = millis();
    publishTelemetry();
  }
}
