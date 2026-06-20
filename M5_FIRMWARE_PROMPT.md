# M5Core2 + SCD41 ファームウェア設計プロンプト

> これは **別の M5 開発環境（Arduino / PlatformIO 等）に貼り付けて使う依頼プロンプト**です。
> 対向のホスト（Raspberry Pi 上の IEEE 1451.1.6 NCAP）側は既に実装・検証済みで、
> ここに書いた **MQTT 契約**どおりに送受信します。M5 側はこの契約に厳密に合わせ、
> **「正しく接続できていること」を起動時に自己検証して画面に表示**してください。

---

## あなたへの依頼（要約）

M5Stack **Core2** に **M5 SCD41（温度・湿度・CO2）ユニット**を接続した端末のファームウェアを設計・実装してください。要件は次の4点です。

1. SCD41 から **温度・湿度・CO2** を定期取得し、WiFi 経由の **MQTT** でホストへ送る。
2. ホストからの **ゲージ指令（0–100）** を受信し、**画面のゲージ（針/バー）を動かす**。
3. 画面に **温度・湿度・CO2・ゲージ値** の **4 つ**を常時表示する。
4. 起動時に **WiFi / MQTT / SCD41 の接続が正しく成立したかを自己検証**し、結果を画面と MQTT で明示する（後述の受け入れ基準を満たすこと）。

同一ファームを **2 台**に書き込みます。`DEVICE_ID` だけを `m5-01` と `m5-02` に変えて使えるようにしてください。

---

## ハードウェア前提

- 本体: **M5Stack Core2**（320×240 LCD、ILI9342C / M5GFX）。
- センサ: **M5 SCD41 Unit**（Sensirion SCD41、I2C アドレス **0x62**、Port.A = **GPIO32(SDA)/GPIO33(SCL)**、3.3V/Grove）。
- 推奨フレームワーク: **Arduino (ESP32)**。ライブラリは M5Unified、PubSubClient（MQTT）、ArduinoJson、Sensirion I2C SCD4x（または `Sensirion_Gadget_BLE` ではなく `sensirion/arduino-i2c-scd4x`）。
  - UIFlow/MicroPython でも可だが、カスタムゲージ描画と自己診断表示の自由度から **Arduino を推奨**。

---

## ネットワーク／MQTT 契約（★ホスト側と一致必須・変更不可）

- ブローカ: 既定 **`broker.hivemq.com:1883`**（TLS なし、認証なし）。`MQTT_HOST`/`MQTT_PORT` で変更可能にすること。
- トピック接頭辞: **`m5iot/`**（定数 `TOPIC_PREFIX`）。
- 端末識別子: **`DEVICE_ID`**（`m5-01` / `m5-02`）。

| 方向 | トピック | ペイロード |
|---|---|---|
| M5 → ホスト | `m5iot/<DEVICE_ID>/telemetry` | JSON: `{"temp":<℃float>,"humid":<%float>,"co2":<ppm int>,"gauge":<0-100 float>}` |
| ホスト → M5 | `m5iot/<DEVICE_ID>/gauge` | 平文の数値文字列（例 `42` または `42.0`）。範囲 **0–100**。 |
| M5 → ホスト | `m5iot/<DEVICE_ID>/status` | `"online"` / `"offline"`。**retain=true**。**LWT は `"offline"`（retain=true）**。 |

詳細・注意:
- **温度は ℃ のまま**送ること（ホストが TEDS に合わせて K へ +273.2 変換する）。湿度は %RH、CO2 は ppm の整数。
- `telemetry` の `gauge` フィールドには **現在画面に表示しているゲージ値**を入れる（ホストはこれを読み戻しに使う）。
- **publish 間隔は 2 秒**（`TELEMETRY_PERIOD_MS = 2000`、定数で変更可）。SCD41 の測定周期（約 5 秒）に対しては、最新の有効測定値を保持して送ること。
- `gauge` 指令受信時は **即座に**画面のゲージを更新し、次回 `telemetry` に新しい `gauge` 値を反映する。
- MQTT client id は `DEVICE_ID` ベースで一意にすること（例 `m5-01-core2`）。

### LWT・接続状態の扱い（接続検証の中核）
- MQTT 接続時の LWT(will) を `topic=m5iot/<id>/status, payload="offline", retain=true, qos=0` で設定。
- 接続成功直後に `status="online"`(retain=true) を publish。
- 切断検出時は再接続をリトライ（指数バックオフ等）。再接続のたびに `online` を再 publish。

---

## 画面表示（4 値 + 接続状態）

- 常時表示する 4 値（大きく、単位つき）:
  - **Temp**: `xx.x °C`
  - **Humid**: `xx.x %`
  - **CO2**: `xxxx ppm`（必要なら色で警告: 例 >1000 黄, >2000 赤）
  - **Gauge**: `xx`（0–100）— **円弧ゲージ or 横バー**を針/塗りで描画し、数値も併記。
- 画面上部または隅に **接続ステータス**を常時表示:
  - `WiFi: SSID (IP)` と ✓/✗
  - `MQTT: connected/▲retry` と ✓/✗
  - `SCD41: ok/✗`
  - `DEVICE_ID` を表示。
- ゲージ指令を受けたら針が動くことが目視で分かること（アニメーション/即時反映どちらでも可）。

---

## ★ 起動時 自己検証シーケンス（「正しく接続できること」の保証）

ファームは起動時に次の **ブートセルフテスト**を行い、各段階の **PASS/FAIL を画面に列挙**してから通常動作へ移ること。いずれか FAIL なら画面に赤で理由を表示し、リトライする（ハングしない）。

1. **SCD41 検出**: I2C 0x62 応答確認 → シリアル番号取得 → 定期測定開始。最初の有効測定が得られるまで「SCD41: warming up」を表示。失敗なら `SCD41: FAIL (addr/wiring)`。
2. **WiFi 接続**: SSID へ接続し IP 取得。タイムアウト時は再試行。成功で `WiFi: PASS (IP)`。
3. **MQTT 接続**: LWT を設定して接続（CONNACK 確認）。`MQTT: PASS`。
4. **購読確認**: `m5iot/<id>/gauge` を subscribe（SUBACK 確認）。`SUB: PASS`。
5. **ラウンドトリップ自己検査**: `status="online"` を publish 後、**自分の `telemetry` を 1 回 publish→（ループバック確認用に）自分の status トピックを read**、もしくは少なくとも publish が `rc=0` で成功することを確認。`PUB: PASS`。
6. すべて PASS したら画面を通常 4 値表示へ切替。

> 補足: ホスト（NCAP）が起動していれば、NCAP のログに
> `[M5] status <id> = online` と `[M5] telemetry <id> ...`、`[SAMPLE] ... JPN-M5-UNIT1:ch1=...`
> が出ます。これも接続成功の外形的証拠として利用してよい。

---

## 受け入れ基準（このとおり動けば完成）

対向ホストは次で起動されている前提（開発者が用意済み。M5 側の検証に使う）:
```
python3 NCAP.py -v          # 実機 Pi（または -p でPC上のpseudo）
```
そのうえで:

1. M5 を電源投入 → ブートセルフテストが全 PASS、4 値画面へ遷移する。
2. NCAP のログに `[M5] status m5-01 = online` と 2 秒ごとの `[M5] telemetry m5-01 ...` が出る。
3. ホストから読み出し（`python3 APP.py --only read --tim 3 --ch 3` 等）で **CO2 実測値**が返る（TIM3=m5-01, TIM4=m5-02。ch1=温度K, ch2=湿度, ch3=CO2, ch4=ゲージ）。
4. ホストからゲージ書込み（`python3 APP.py --only write --tim 3 --ch 4 -w 42`）で **M5 画面のゲージが 42 に動く**。続けて値を変えると追従する。
5. M5 の電源を抜く/WiFi を切ると、retain された `status` が **LWT により `offline`** になり、NCAP の `[SAMPLE]` から該当 ch の実値が消える（pseudo 値へフォールバック、または欠落）。
6. 2 台（`m5-01`/`m5-02`）を同時稼働させても、互いのトピックが混ざらず独立に表示・制御できる。

---

## 成果物として提出してほしいもの

1. ビルド可能な **Arduino スケッチ（.ino）または PlatformIO プロジェクト**一式。
2. 先頭に **設定定数ブロック**（`WIFI_SSID`, `WIFI_PASS`, `MQTT_HOST`, `MQTT_PORT`, `DEVICE_ID`, `TOPIC_PREFIX`, `TELEMETRY_PERIOD_MS`）。確定値は次のとおり:
   ```cpp
   #define WIFI_SSID           "westexp-mobile"
   #define WIFI_PASS           "kyouryokunawestnohashi"
   #define MQTT_HOST           "broker.hivemq.com"   // ホストの config.yml と一致
   #define MQTT_PORT           1883
   #define TOPIC_PREFIX        "m5iot/"
   #define DEVICE_ID           "m5-01"               // 2台目は "m5-02"
   #define TELEMETRY_PERIOD_MS 2000
   ```
3. 使用ライブラリと**バージョン**、書き込み手順（ボード設定 = M5Core2、Upload speed 等）。
4. `DEVICE_ID` を `m5-01`/`m5-02` に変えて 2 台に焼く手順。
5. 上記**受け入れ基準を自分で確認した結果**（各項目 PASS のスクショ or ログ）。
6. 既知の制約・代替案（例: SCD41 測定周期と publish 周期の差、WiFi 再接続時の挙動）を README に明記。

---

## 運用・取り扱い上の指示（必須）

1. **認証情報の秘匿**: `WIFI_PASS` 等を平文でリポジトリにコミットしないこと。
   - 認証情報は **別ファイルに分離**する（例: `secrets.h` に `WIFI_SSID/WIFI_PASS` を置き `#include "secrets.h"`）。
   - その `secrets.h`（および本プロンプト由来の平文パスワードを含むファイル）は **`.gitignore` に追加**し、公開リポジトリへ入れない。配布・共有時はパスワード部分を伏せる。
   - PlatformIO なら `build_flags`/環境変数経由で渡す方式でもよい。
2. **ブローカの一致**: M5 の `MQTT_HOST`/`MQTT_PORT` は、対向ホスト（NCAP）の `config.yml` の `mqtthost`/`mqttport` と**必ず一致**させること。
   - 既定は公開ブローカ `broker.hivemq.com:1883`。
   - 同一 WiFi 内の**ローカルブローカ**（例: Mosquitto）に切り替える場合は、**M5 側とホスト側の両方**を同じアドレスへ変更する（片方だけ変えると疎通しない）。ローカルブローカ採用時は、両端末・ホストが同一ネットワークに居ることを確認する。

## 実装ヒント（任意）

- PubSubClient は `setBufferSize(256)` 以上に拡張（JSON が切れないように）。
- `loop()` ではブロッキング `delay` を避け、`millis()` で telemetry 周期と MQTT `loop()`/再接続を回す。
- SCD41 は `startPeriodicMeasurement()` 後、`getDataReadyFlag()` を見てから `readMeasurement()`。
- ゲージ描画はちらつき防止にスプライト（M5Canvas）推奨。
- CO2 を色分け（緑/黄/赤）すると換気デモとして映える。
- 温度は **℃ のまま送る**点を再強調（ホストが K 変換する）。
