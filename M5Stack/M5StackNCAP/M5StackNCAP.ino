#include <M5Core2.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include "SensirionI2cScd4x.h"
#include <Wire.h>

// Wi-Fi設定
const char* ssid = "westexp-mobile";
const char* password = "kyouryokunawestnohashi";

// MQTTブローカー設定（動的に変更可能）
String mqtt_server = "broker.hivemq.com";
int mqtt_port = 1883;

// Device name（MACアドレスから自動生成）
String device_name;
String topic_name;

SensirionI2cScd4x scd4x;

// MQTT client
WiFiClient espClient;
PubSubClient client(espClient);

// 表示モード: 0=グラフ, 1=デバッグ情報, 2=設定画面
int displayMode = 0;

// デバッグ情報を保存
String debugInfo[20];
int debugInfoCount = 0;

// グラフ設定
#define GRAPH_WIDTH  268
#define GRAPH_HEIGHT 48
#define GRAPH_X      42
#define GRAPH_SPACING 5

// 各グラフとキャプションのY座標を定義
#define CAPTION_CO2_Y   35
#define GRAPH_CO2_Y     (CAPTION_CO2_Y + 10)
#define CAPTION_TEMP_Y  (GRAPH_CO2_Y + GRAPH_HEIGHT + GRAPH_SPACING)
#define GRAPH_TEMP_Y    (CAPTION_TEMP_Y + 10)
#define CAPTION_HUM_Y   (GRAPH_TEMP_Y + GRAPH_HEIGHT + GRAPH_SPACING)
#define GRAPH_HUM_Y     (CAPTION_HUM_Y + 10)

float co2_values[GRAPH_WIDTH];
float temp_values[GRAPH_WIDTH];
float hum_values[GRAPH_WIDTH];

// キーボード設定
#define KEYBOARD_X (2)
#define KEYBOARD_Y (80)
#define KEY_W (45)
#define KEY_H (40)
#define COLS (7)
#define ROWS (4)
#define MAX_SHIFT_MODE (4)

char keymap[MAX_SHIFT_MODE][ROWS][COLS] =
{
  {
    {'a', 'b', 'c', 'd', 'e', 'f', 'g'},
    {'h', 'i', 'j', 'k', 'l', 'm', 'n'},
    {'o', 'p', 'q', 'r', 's', 't', 'u'},
    {'v', 'w', 'x', 'y', 'z', 3, 2},
  },
  {
    {'A', 'B', 'C', 'D', 'E', 'F', 'G'},
    {'H', 'I', 'J', 'K', 'L', 'M', 'N'},
    {'O', 'P', 'Q', 'R', 'S', 'T', 'U'},
    {'V', 'W', 'X', 'Y', 'Z', 3, 2},
  },
  {
    {'`', '1', '2', '3', '4', '5', '6'},
    {'7', '8', '9', '0', '-', '=', '['},
    {']', 92, ';', 39, ',', '.', '/'},
    {' ', ' ', ' ', ' ', ' ', 3, 2},
  },
  {
    {'~', '!', '@', '#', '$', '%', '^'},
    {'&', '*', '(', ')', '_', '+', '{'},
    {'}', '|', ':', '"', '<', '>', '?'},
    {' ', ' ', ' ', ' ', ' ', 3, 2},
  },
};

typedef enum {
  KEY_MODE_LETTER = 0,
  KEY_MODE_NUMBER = 1,
} key_mode_t;

String input_text;
key_mode_t key_mode = KEY_MODE_LETTER;
bool shift_mode = false;
int config_step = 0;

// プリセット設定
String broker_presets[] = {
  "broker.hivemq.com",
  "192.168.8.100",
  "192.168.8.10"
};
int broker_preset_index = 0;
const int BROKER_PRESET_COUNT = 3;

String topic_presets[4];
int topic_preset_index = 0;
const int TOPIC_PRESET_COUNT = 3;

// デバッグ情報を追加する関数
void addDebugInfo(String info) {
  if (debugInfoCount < 20) {
    debugInfo[debugInfoCount++] = info;
  }
  Serial.println(info);
}

void setup_wifi() {
  delay(10);
  
  addDebugInfo("=== WiFi Setup ===");
  addDebugInfo("SSID: " + String(ssid));
  
  uint8_t mac[6];
  WiFi.macAddress(mac);
  char macStr[18];
  sprintf(macStr, "%02X:%02X:%02X:%02X:%02X:%02X", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  addDebugInfo("MAC Address: " + String(macStr));
  
  addDebugInfo("Connecting to WiFi...");

  WiFi.begin(ssid, password);

  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 30) {
    delay(500);
    Serial.print(".");
    attempts++;
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    addDebugInfo("WiFi Connected!");
    addDebugInfo("IP Address: " + WiFi.localIP().toString());
    addDebugInfo("Gateway: " + WiFi.gatewayIP().toString());
    addDebugInfo("Subnet Mask: " + WiFi.subnetMask().toString());
    addDebugInfo("DNS: " + WiFi.dnsIP().toString());
    addDebugInfo("Signal Strength: " + String(WiFi.RSSI()) + " dBm");
  } else {
    addDebugInfo("WiFi Connection Failed!");
  }
}

void reconnect() {
  while (!client.connected()) {
    addDebugInfo("=== MQTT Connection ===");
    addDebugInfo("Server: " + mqtt_server);
    addDebugInfo("Port: " + String(mqtt_port));
    addDebugInfo("Client ID: " + device_name);
    addDebugInfo("Attempting MQTT connection...");
    
    if (client.connect(device_name.c_str())) {
      addDebugInfo("MQTT Connected!");
      addDebugInfo("Publishing to: " + topic_name);
    } else {
      String errorMsg = "MQTT Failed, rc=" + String(client.state());
      addDebugInfo(errorMsg);
      
      switch(client.state()) {
        case -4: addDebugInfo("Error: Connection timeout"); break;
        case -3: addDebugInfo("Error: Connection lost"); break;
        case -2: addDebugInfo("Error: Connect failed"); break;
        case -1: addDebugInfo("Error: Disconnected"); break;
        case 1: addDebugInfo("Error: Bad protocol"); break;
        case 2: addDebugInfo("Error: Bad client ID"); break;
        case 3: addDebugInfo("Error: Unavailable"); break;
        case 4: addDebugInfo("Error: Bad credentials"); break;
        case 5: addDebugInfo("Error: Unauthorized"); break;
      }
      
      addDebugInfo("Retrying in 5 seconds...");
      delay(5000);
    }
  }
}

void displayDebugInfo() {
  M5.Lcd.fillScreen(BLACK);
  
  M5.Lcd.setTextColor(WHITE, BLACK);
  M5.Lcd.setTextSize(2);
  M5.Lcd.setCursor(10, 10);
  M5.Lcd.printf("Device: %s", device_name.c_str());
  
  M5.Lcd.setTextColor(GREEN, BLACK);
  M5.Lcd.setTextSize(1);
  M5.Lcd.setCursor(0, 30);
  M5.Lcd.println("=== DEBUG INFORMATION ===");
  M5.Lcd.println();
  
  M5.Lcd.setTextColor(WHITE, BLACK);
  for (int i = 0; i < debugInfoCount && i < 16; i++) {
    M5.Lcd.println(debugInfo[i]);
  }
  
  M5.Lcd.setTextColor(YELLOW, BLACK);
  M5.Lcd.setCursor(0, 220);
  M5.Lcd.println("A:Graph C:Config");
}

void displayGraphMode() {
  M5.Lcd.fillScreen(BLACK);
  
  M5.Lcd.setTextColor(WHITE, BLACK);
  M5.Lcd.setTextSize(2);
  M5.Lcd.setCursor(10, 10);
  M5.Lcd.printf("Device: %s", device_name.c_str());
  
  M5.Lcd.setTextSize(1);
  M5.Lcd.setTextColor(WHITE, BLACK);

  M5.Lcd.setCursor(GRAPH_X, CAPTION_CO2_Y);
  M5.Lcd.print("CO2 Concentration (ppm)");
  M5.Lcd.drawRect(GRAPH_X - 2, GRAPH_CO2_Y - 2, GRAPH_WIDTH + 4, GRAPH_HEIGHT + 4, WHITE);
  M5.Lcd.setCursor(5, GRAPH_CO2_Y);
  M5.Lcd.print("5000");
  M5.Lcd.setCursor(5, GRAPH_CO2_Y + GRAPH_HEIGHT / 2 - 4);
  M5.Lcd.print("2500");
  M5.Lcd.setCursor(15, GRAPH_CO2_Y + GRAPH_HEIGHT - 8);
  M5.Lcd.print("0");

  M5.Lcd.setCursor(GRAPH_X, CAPTION_TEMP_Y);
  M5.Lcd.print("Temperature (C)");
  M5.Lcd.drawRect(GRAPH_X - 2, GRAPH_TEMP_Y - 2, GRAPH_WIDTH + 4, GRAPH_HEIGHT + 4, WHITE);
  M5.Lcd.setCursor(15, GRAPH_TEMP_Y);
  M5.Lcd.print("40");
  M5.Lcd.setCursor(15, GRAPH_TEMP_Y + GRAPH_HEIGHT / 2 - 4);
  M5.Lcd.print("15");
  M5.Lcd.setCursor(10, GRAPH_TEMP_Y + GRAPH_HEIGHT - 8);
  M5.Lcd.print("-10");

  M5.Lcd.setCursor(GRAPH_X, CAPTION_HUM_Y);
  M5.Lcd.print("Humidity (%)");
  M5.Lcd.drawRect(GRAPH_X - 2, GRAPH_HUM_Y - 2, GRAPH_WIDTH + 4, GRAPH_HEIGHT + 4, WHITE);
  M5.Lcd.setCursor(10, GRAPH_HUM_Y);
  M5.Lcd.print("100");
  M5.Lcd.setCursor(15, GRAPH_HUM_Y + GRAPH_HEIGHT / 2 - 4);
  M5.Lcd.print("50");
  M5.Lcd.setCursor(15, GRAPH_HUM_Y + GRAPH_HEIGHT - 8);
  M5.Lcd.print("0");
}

void drawKeyboard() {
  int x, y;

  M5.Lcd.fillRect(KEYBOARD_X, KEYBOARD_Y, COLS * KEY_W, ROWS * KEY_H, TFT_BLACK);

  for(int r = 0; r < ROWS; r++) {
    for(int c = 0; c < COLS; c++) {
      x = (KEYBOARD_X + (c * KEY_W));
      y = (KEYBOARD_Y + (r * KEY_H));
      M5.Lcd.drawRoundRect(x, y, KEY_W, KEY_H, 10, TFT_DARKGREY);

      int key_page = 0;
      if(key_mode == KEY_MODE_NUMBER) key_page += 2;
      if(shift_mode == true) key_page += 1;

      char ch = keymap[key_page][r][c];

      M5.Lcd.setTextColor(TFT_WHITE, TFT_BLACK);
      if(ch == 2) {
        M5.Lcd.setTextSize(1);
        M5.Lcd.setCursor(x + 9, y + 18);
        M5.Lcd.print("shift");
      } else if(ch == 3) {
        M5.Lcd.setTextSize(1);
        M5.Lcd.setCursor(x + 12, y + 18);
        M5.Lcd.print("del");
      } else {
        M5.Lcd.setTextSize(2);
        M5.Lcd.setCursor(x + 15, y + 12);
        M5.Lcd.print(ch);
      }
    }
  }
}

bool isValidBroker(String input) {
  if (input.length() == 0) return false;
  input.trim();
  if (input.length() == 0) return false;
  if (input.indexOf(' ') >= 0) return false;
  if (input.length() < 3) return false;
  return true;
}

bool isValidTopic(String input) {
  if (input.length() == 0) return false;
  input.trim();
  if (input.length() == 0) return false;
  for (int i = 0; i < input.length(); i++) {
    char c = input.charAt(i);
    if (c < 33 || c > 126) return false;
  }
  return true;
}

void showErrorMessage(String message) {
  M5.Lcd.fillRect(0, 220, M5.Lcd.width(), 20, TFT_RED);
  M5.Lcd.setTextSize(1);
  M5.Lcd.setTextColor(TFT_WHITE, TFT_RED);
  M5.Lcd.setCursor(10, 225);
  M5.Lcd.print(message);
  delay(2000);
  
  M5.Lcd.fillRect(0, 220, M5.Lcd.width(), 20, TFT_BLACK);
  M5.Lcd.setTextColor(TFT_GREEN, TFT_BLACK);
  M5.Lcd.setCursor(35, 225);
  M5.Lcd.print("preset");
  M5.Lcd.setCursor(145, 225);
  M5.Lcd.print("mode");
  M5.Lcd.setCursor(245, 225);
  M5.Lcd.print("enter");
}

void initConfigMode() {
  M5.Lcd.fillScreen(TFT_BLACK);
  M5.Lcd.setTextSize(1);

  M5.Lcd.setTextColor(TFT_YELLOW, TFT_BLACK);
  M5.Lcd.setCursor(5, 10);
  if (config_step == 0) {
    M5.Lcd.print("Enter MQTT Broker:");
    M5.Lcd.setCursor(5, 30);
    M5.Lcd.setTextColor(TFT_WHITE, TFT_BLACK);
    M5.Lcd.print("Current: " + mqtt_server);
    M5.Lcd.setCursor(5, 45);
    M5.Lcd.setTextColor(TFT_CYAN, TFT_BLACK);
    M5.Lcd.printf("Preset[%d/%d]: %s", broker_preset_index + 1, BROKER_PRESET_COUNT, broker_presets[broker_preset_index].c_str());
  } else {
    M5.Lcd.print("Enter Topic Name:");
    M5.Lcd.setCursor(5, 30);
    M5.Lcd.setTextColor(TFT_WHITE, TFT_BLACK);
    M5.Lcd.print("Current: " + topic_name);
    M5.Lcd.setCursor(5, 45);
    M5.Lcd.setTextColor(TFT_CYAN, TFT_BLACK);
    M5.Lcd.printf("Preset[%d/%d]: %s", topic_preset_index + 1, TOPIC_PRESET_COUNT, topic_presets[topic_preset_index].c_str());
  }

  M5.Lcd.setCursor(5, 65);
  M5.Lcd.setTextColor(TFT_GREEN, TFT_BLACK);
  M5.Lcd.setTextSize(2);
  M5.Lcd.print(input_text);

  M5.Lcd.setTextSize(1);
  M5.Lcd.setTextColor(TFT_GREEN, TFT_BLACK);
  M5.Lcd.setCursor(35, 225);
  M5.Lcd.print("preset");
  M5.Lcd.setCursor(145, 225);
  M5.Lcd.print("mode");
  M5.Lcd.setCursor(245, 225);
  M5.Lcd.print("enter");

  key_mode = KEY_MODE_LETTER;
  shift_mode = false;
  drawKeyboard();
}

void handleConfigInput() {
  if (M5.BtnA.wasPressed()) {
    if (config_step == 0) {
      broker_preset_index = (broker_preset_index + 1) % BROKER_PRESET_COUNT;
      input_text = broker_presets[broker_preset_index];
    } else {
      topic_preset_index = (topic_preset_index + 1) % TOPIC_PRESET_COUNT;
      input_text = topic_presets[topic_preset_index];
    }
    initConfigMode();
    return;
  }
  
  if (M5.BtnB.wasPressed()) {
    if (key_mode == KEY_MODE_LETTER) {
      key_mode = KEY_MODE_NUMBER;
    } else {
      key_mode = KEY_MODE_LETTER;
    }
    shift_mode = false;
    drawKeyboard();
    return;
  }
  
  if (M5.BtnC.wasPressed()) {
    if (config_step == 0) {
      if (input_text.length() > 0) {
        input_text.trim();
        if (isValidBroker(input_text)) {
          mqtt_server = input_text;
          client.disconnect();
          client.setServer(mqtt_server.c_str(), mqtt_port);
          addDebugInfo("MQTT Broker changed to: " + mqtt_server);
          input_text = "";
          config_step = 1;
          initConfigMode();
        } else {
          showErrorMessage("Invalid broker! No spaces allowed");
          input_text = "";
          M5.Lcd.fillRect(0, 65, M5.Lcd.width(), 20, TFT_BLACK);
        }
      } else {
        input_text = "";
        config_step = 1;
        initConfigMode();
      }
    } else {
      if (input_text.length() > 0) {
        input_text.trim();
        if (isValidTopic(input_text)) {
          topic_name = input_text;
          addDebugInfo("Topic changed to: " + topic_name);
          input_text = "";
          config_step = 0;
          displayMode = 0;
          displayGraphMode();
        } else {
          showErrorMessage("Invalid topic! No spaces/control chars");
          input_text = "";
          M5.Lcd.fillRect(0, 65, M5.Lcd.width(), 20, TFT_BLACK);
        }
      } else {
        input_text = "";
        config_step = 0;
        displayMode = 0;
        displayGraphMode();
      }
    }
    return;
  }
  
  TouchPoint_t pos = M5.Touch.getPressPoint();
  if (pos.y > 0 && pos.x > 0) {
    if (pos.y >= KEYBOARD_Y && pos.y < (KEYBOARD_Y + ROWS * KEY_H)) {
      int col = (pos.x - KEYBOARD_X) / KEY_W;
      int row = (pos.y - KEYBOARD_Y) / KEY_H;
      
      if (col >= 0 && col < COLS && row >= 0 && row < ROWS) {
        int key_page = 0;
        if(key_mode == KEY_MODE_NUMBER) key_page += 2;
        if(shift_mode == true) key_page += 1;
        
        char ch = keymap[key_page][row][col];
        
        if(ch == 2) {
          shift_mode = !shift_mode;
          drawKeyboard();
        } else if(ch == 3) {
          if (input_text.length() > 0) {
            input_text = input_text.substring(0, input_text.length() - 1);
            M5.Lcd.fillRect(0, 65, M5.Lcd.width(), 20, TFT_BLACK);
            M5.Lcd.setCursor(5, 65);
            M5.Lcd.setTextSize(2);
            M5.Lcd.setTextColor(TFT_GREEN, TFT_BLACK);
            M5.Lcd.print(input_text);
          }
        } else {
          input_text += ch;
          M5.Lcd.fillRect(0, 65, M5.Lcd.width(), 20, TFT_BLACK);
          M5.Lcd.setCursor(5, 65);
          M5.Lcd.setTextSize(2);
          M5.Lcd.setTextColor(TFT_GREEN, TFT_BLACK);
          M5.Lcd.print(input_text);
        }
        delay(200);
      }
    }
  }
}

void setup() {
  M5.begin();
  Serial.begin(115200);
  while (!Serial) {
    delay(100);
  }

  addDebugInfo("=== M5Core2 Starting ===");

  uint8_t mac[6];
  WiFi.macAddress(mac);
  char mac_suffix[5];
  sprintf(mac_suffix, "%02X%02X", mac[4], mac[5]);
  device_name = "Core2_" + String(mac_suffix);
  
  topic_name = "_IECON_Plugfest/" + device_name + "/sample";

  topic_presets[0] = "_IECON_Plugfest/" + device_name + "/sample";
  topic_presets[1] = "_IECON_Plugfest/M1/env";
  topic_presets[2] = "_IECON_Plugfest/M2/env";

  addDebugInfo("Device Name: " + device_name);
  addDebugInfo("Topic Name: " + topic_name);

  M5.Lcd.fillScreen(BLACK);
  M5.Lcd.setTextColor(WHITE, BLACK);
  M5.Lcd.setTextSize(2);
  M5.Lcd.setCursor(10, 10);
  M5.Lcd.printf("Device: %s", device_name.c_str());

  setup_wifi();

  client.setServer(mqtt_server.c_str(), mqtt_port);
  addDebugInfo("MQTT Server Set: " + mqtt_server + ":" + String(mqtt_port));

  Wire.begin();
  addDebugInfo("=== I2C Initialized ===");

  uint16_t error;
  char errorMessage[256];

  scd4x.begin(Wire, 0x62);
  addDebugInfo("SCD4x Sensor at 0x62");

  error = scd4x.stopPeriodicMeasurement();
  if (error) {
    errorToString(error, errorMessage, 256);
    addDebugInfo("SCD4x Stop Error: " + String(errorMessage));
  } else {
    addDebugInfo("SCD4x Stopped");
  }

  uint64_t serialNumber;
  error = scd4x.getSerialNumber(serialNumber);
  if (error) {
    errorToString(error, errorMessage, 256);
    addDebugInfo("Serial Read Error: " + String(errorMessage));
  } else {
    char serialStr[20];
    sprintf(serialStr, "0x%llX", serialNumber);
    addDebugInfo("Sensor Serial: " + String(serialStr));
  }

  error = scd4x.startPeriodicMeasurement();
  if (error) {
    errorToString(error, errorMessage, 256);
    addDebugInfo("Start Measurement Error: " + String(errorMessage));
  } else {
    addDebugInfo("Measurement Started");
  }

  addDebugInfo("Waiting for first data...");

  for (int i = 0; i < GRAPH_WIDTH; i++) {
    co2_values[i] = 0;
    temp_values[i] = 0;
    hum_values[i] = 0;
  }

  displayGraphMode();
}

void loop() {
  M5.update();
  
  if (displayMode == 2) {
    handleConfigInput();
    return;
  }
  
  if (M5.BtnA.wasPressed()) {
    displayMode = (displayMode == 0) ? 1 : 0;
    if (displayMode == 1) {
      displayDebugInfo();
    } else {
      displayGraphMode();
    }
  }
  
  if (M5.BtnC.wasPressed()) {
    displayMode = 2;
    input_text = "";
    config_step = 0;
    initConfigMode();
    delay(200);
    return;
  }

  if (displayMode == 1) {
    delay(100);
    return;
  }

  if (!client.connected()) {
    reconnect();
  }
  client.loop();

  uint16_t error;
  char errorMessage[256];

  delay(100);

  uint16_t co2 = 0;
  float temperature = 0.0f;
  float humidity = 0.0f;
  bool isDataReady = false;
  error = scd4x.getDataReadyStatus(isDataReady);
  if (error) {
    Serial.print("Error trying to execute getDataReadyStatus(): ");
    errorToString(error, errorMessage, 256);
    Serial.println(errorMessage);
    return;
  }
  if (!isDataReady) {
    return;
  }
  error = scd4x.readMeasurement(co2, temperature, humidity);
  if (error) {
    Serial.print("Error trying to execute readMeasurement(): ");
    errorToString(error, errorMessage, 256);
    Serial.println(errorMessage);
  } else if (co2 == 0) {
    Serial.println("Invalid sample detected, skipping.");
  } else {
    Serial.println("=== Sensor Reading ===");
    Serial.print("CO2: ");
    Serial.print(co2);
    Serial.print(" ppm\t");
    Serial.print("Temperature: ");
    Serial.print(temperature);
    Serial.print(" C\t");
    Serial.print("Humidity: ");
    Serial.print(humidity);
    Serial.println(" %");

    for (int i = 0; i < GRAPH_WIDTH - 1; i++) {
      co2_values[i] = co2_values[i + 1];
      temp_values[i] = temp_values[i + 1];
      hum_values[i] = hum_values[i + 1];
    }
    co2_values[GRAPH_WIDTH - 1] = co2;
    temp_values[GRAPH_WIDTH - 1] = temperature;
    hum_values[GRAPH_WIDTH - 1] = humidity;

    M5.Lcd.fillRect(GRAPH_X, GRAPH_CO2_Y, GRAPH_WIDTH, GRAPH_HEIGHT, BLACK);
    M5.Lcd.fillRect(GRAPH_X, GRAPH_TEMP_Y, GRAPH_WIDTH, GRAPH_HEIGHT, BLACK);
    M5.Lcd.fillRect(GRAPH_X, GRAPH_HUM_Y, GRAPH_WIDTH, GRAPH_HEIGHT, BLACK);

    for (int i = 0; i < GRAPH_WIDTH - 1; i++) {
      int y1 = GRAPH_CO2_Y + GRAPH_HEIGHT - (co2_values[i] / 5000.0) * GRAPH_HEIGHT;
      int y2 = GRAPH_CO2_Y + GRAPH_HEIGHT - (co2_values[i + 1] / 5000.0) * GRAPH_HEIGHT;
      M5.Lcd.drawLine(GRAPH_X + i, y1, GRAPH_X + i + 1, y2, GREEN);
    }

    for (int i = 0; i < GRAPH_WIDTH - 1; i++) {
      int y1 = GRAPH_TEMP_Y + GRAPH_HEIGHT - ((temp_values[i] + 10) / 50.0) * GRAPH_HEIGHT;
      int y2 = GRAPH_TEMP_Y + GRAPH_HEIGHT - ((temp_values[i + 1] + 10) / 50.0) * GRAPH_HEIGHT;
      M5.Lcd.drawLine(GRAPH_X + i, y1, GRAPH_X + i + 1, y2, RED);
    }

    for (int i = 0; i < GRAPH_WIDTH - 1; i++) {
      int y1 = GRAPH_HUM_Y + GRAPH_HEIGHT - (hum_values[i] / 100.0) * GRAPH_HEIGHT;
      int y2 = GRAPH_HUM_Y + GRAPH_HEIGHT - (hum_values[i + 1] / 100.0) * GRAPH_HEIGHT;
      M5.Lcd.drawLine(GRAPH_X + i, y1, GRAPH_X + i + 1, y2, BLUE);
    }

    M5.Lcd.setTextColor(WHITE, BLACK);
    M5.Lcd.fillRect(10, GRAPH_HUM_Y + GRAPH_HEIGHT + 12, 300, 20, BLACK);
    M5.Lcd.setCursor(10, GRAPH_HUM_Y + GRAPH_HEIGHT + 12);
    M5.Lcd.printf("Keio Univ. WestLab  CO2:%d Temp:%.1fC Hum:%.1f%%", co2, temperature, humidity);

    char mqttMessage[128];
    snprintf(mqttMessage, sizeof(mqttMessage), "{\"device\":\"%s\",\"co2\":%d,\"temperature\":%.2f,\"humidity\":%.2f}", device_name.c_str(), co2, temperature, humidity);

    if (client.connected()) {
      Serial.println("=== MQTT Publish ===");
      Serial.print("Topic: ");
      Serial.println(topic_name);
      Serial.print("Message: ");
      Serial.println(mqttMessage);
      
      if (!client.publish(topic_name.c_str(), mqttMessage)) {
        Serial.println("MQTT Publish Failed!");
      } else {
        Serial.println("MQTT Published Successfully");
      }
    } else {
      Serial.println("MQTT client not connected - skipping publish");
    }
  }
}