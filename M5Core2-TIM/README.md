# M5Core2 + SCD41 → IEEE 1451.1.6 NCAP (MQTT) ファームウェア

M5Stack Core2 に M5 SCD41 ユニット（温度・湿度・CO2）を接続し、WiFi/MQTT で
ホスト（Raspberry Pi 上の NCAP）と通信する端末ファームウェアです。
`M5_FIRMWARE_PROMPT.md` の MQTT 契約・受け入れ基準に厳密に準拠しています。

## 機能

- SCD41 から温度・湿度・CO2 を定期取得（I2C 0x62, Port.A = GPIO32/33）。
- MQTT で 2 秒ごとに telemetry を publish。
- ホストからのゲージ指令（0–100）を受信し、画面の横バーゲージを即時更新。
- 画面に 4 値（Temp / Humid / CO2 / Gauge）＋接続状態（WiFi / MQTT / SCD41）を常時表示。
- 起動時セルフテスト（SCD41 → WiFi → MQTT → SUB → PUB）を画面に PASS/FAIL 列挙。
- LWT `offline`(retain) ＋ 接続直後に `online`(retain) を publish。切断時は指数的に再接続。

## MQTT 契約（ホストと一致）

| 方向 | トピック | ペイロード |
|---|---|---|
| M5 → host | `m5iot/<DEVICE_ID>/telemetry` | `{"temp":℃,"humid":%,"co2":ppm,"gauge":0-100}` |
| host → M5 | `m5iot/<DEVICE_ID>/gauge` | 平文数値 `0..100`（例 `42`） |
| M5 → host | `m5iot/<DEVICE_ID>/status` | `"online"`/`"offline"`, retain=true, LWT=`"offline"` |

- **温度は ℃ のまま**送信（ホストが K へ変換）。湿度 %RH、CO2 は ppm 整数。
- `telemetry.gauge` には現在画面に表示中のゲージ値を入れる（ホストの読み戻し用）。
- MQTT client id = `<DEVICE_ID>-core2`。バッファは 512B に拡張。

## ファイル構成

- `M5Core2-TIM.ino` — 本体スケッチ。先頭に設定定数ブロック。
- `secrets.h` — WiFi 認証情報（**.gitignore 済み・コミット禁止**）。
- `secrets.h.example` — secrets.h の雛形。
- `.gitignore` — secrets.h とビルド成果物を除外。

## 設定

`M5Core2-TIM.ino` 先頭の定数ブロック：

```cpp
#define MQTT_HOST           "broker.hivemq.com"   // host config.yml の mqtthost と一致
#define MQTT_PORT           1883
#define TOPIC_PREFIX        "m5iot/"
#define DEVICE_ID           "m5-01"               // 2台目は "m5-02"
#define TELEMETRY_PERIOD_MS 2000
```

WiFi 認証情報は `secrets.h` に分離：

```cpp
#define WIFI_SSID  "westexp-mobile"
#define WIFI_PASS  "********"
```

> `MQTT_HOST`/`MQTT_PORT` は secrets.h で `#define` すれば上書き可能（任意）。

## 使用ライブラリ / 環境（検証済みバージョン）

- arduino-cli 1.3.1
- ボードプラットフォーム: `m5stack:esp32` 2.1.4（FQBN `m5stack:esp32:m5stack_core2`）
- M5Unified 0.2.13 / M5GFX 0.2.20
- PubSubClient 2.8
- ArduinoJson 7.4.2
- SCD41 は外部ライブラリ不要（`Wire` で直接ドライブ。CRC-8 0x31 検証つき）

## ビルド & 書き込み（arduino-cli）

```bash
# コンパイル
arduino-cli compile --fqbn m5stack:esp32:m5stack_core2 .

# 書き込み（COM ポートは環境に合わせる。Core2 は CH9102 USB-serial）
arduino-cli upload -p COM12 --fqbn m5stack:esp32:m5stack_core2 .
```

Arduino IDE の場合: ボード = **M5Core2**, Upload Speed = 921600（既定可）, Port = CH9102 の COM。

### 2 台に焼く手順

1. `DEVICE_ID` を `"m5-01"` のまま 1 台目に書き込み。
2. `DEVICE_ID` を `"m5-02"` に変更して再コンパイル → 2 台目に書き込み。
3. トピックが `m5iot/m5-01/...` と `m5iot/m5-02/...` で分離され、独立に動作。

## 画面

- 上部バー: `WiFi OK/X` `MQTT OK/^` `SCD41 OK/X` ＋ `DEVICE_ID / SSID / IP`。
- 中央: Temp（℃）, Humid（%）, CO2（ppm, >1000 黄 / >2000 赤）。
- 下部: ゲージ（横バー、塗りと数値）。指令受信で即時に針が動く。

## 検証結果（実機 m5-01・broker.hivemq.com）

起動セルフテスト（シリアル/画面）:

```
1) SCD41: PASS (measuring)
2) WiFi: PASS (<IP>)
3) MQTT: PASS
4) SUB: PASS
5) PUB: PASS (rc=0)
```

Telemetry（2 秒周期, rc=0）:

```
[TX] m5iot/m5-01/telemetry rc=0 {"temp":33.7,"humid":56.6,"co2":943,"gauge":0}
```

ゲージ書込みのラウンドトリップ（host → M5 → telemetry 反映）:

```
[RX] gauge = 42.0
[RX] gauge = 77.0
[TX] m5iot/m5-01/telemetry rc=0 {"temp":...,"gauge":77}
[RX] gauge = 5.0
[TX] m5iot/m5-01/telemetry rc=0 {"temp":34.5,"humid":54.4,"co2":963,"gauge":5}
```

→ 画面ゲージが追従し、telemetry の `gauge` に即反映されることを確認。

## 既知の制約 / 代替案

- **測定周期 vs publish 周期**: SCD41 の更新は約 5 秒、publish は 2 秒。間の周期では
  最新の有効測定値を保持して送るため、2〜3 回は同じ値が連続する（仕様どおり）。
- **WiFi/MQTT 再接続**: `loop()` 内で 2 秒バックオフで再接続を試行。再接続のたびに
  `online`(retain) を再 publish。切断時は LWT で `offline`(retain) がホストに残る。
- **シリアルログ**: 115200bps で boot/TX/RX を出力（デバッグ用）。USB を開くと
  DTR/RTS で基板がリセットされ、セルフテストが再実行される（正常動作）。
- **公開ブローカ**: 既定の `broker.hivemq.com` は公開のため遅延・混雑があり得る。
  本番は同一 LAN の Mosquitto 等に M5 側・ホスト側の両方を切り替えると安定。
