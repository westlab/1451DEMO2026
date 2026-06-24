# Debug / helper scripts

Developer-only utilities used while building and debugging the IEEE 1451.1.6
demo. **None of these are required to run the demo** — the deliverable is the
NCAP (`../NCAP.py`, `../NCAPmsg.py`, `../NCAPtbl.py`), `../APP.py`,
`../config.yml`, `../NodeRED.json`, and the M5 firmware (`../M5Core2-TIM/`).
They are kept here for reference and reproducibility.

No secrets are stored in any of these files (WiFi/MQTT credentials live only in
the git-ignored `../M5Core2-TIM/secrets.h`).

---

## Node-RED flow builders (`_*.py`)

One-off scripts that shaped the Node-RED dashboard over time. Each one reads the
**live** flow from the Node-RED admin API (`http://127.0.0.1:1880/flows`),
edits it in place, and POSTs it back (so Node-RED must be running locally when
they are used). They are listed roughly in the order they were applied. Re-run
them only to reproduce a historical step; the current result is already baked
into `../NodeRED.json`.

| Script | What it does |
|---|---|
| `_build_full_svc_ui.py` | Add dashboard buttons for **every** 1451.0/1451.1.6 service (C-OP / CSV). |
| `_build_d0_svc_ui.py` | Add D0 (binary) buttons for every service, symmetric to the D0C (CSV) ones. |
| `_unify_d0_encode.py` | Convert inline-binary D0 builders to the spec-driven encode method. |
| `_rebuild_legacy.py` | Rebuild legacy single-read (2,1) and read-TEDS (3,2) buttons to the new method. |
| `_fix_cop_length.py` | Insert the zero-filled C-OP length column into every C-OP builder/parser. |
| `_build_gauge_actuator.py` | Add a working Gauge actuator (sync_write → M5) with a reacting on-screen gauge. |
| `_build_stop_buttons.py` | Add "Unsubscribe (Stop)" buttons: event (4,3,1) + heartbeat (4,12,1). |
| `_split_d0_d0c.py` | Split the single Request-Response UI tab into two tabs: **D0C** and **D0**. |
| `_func_groups.py` | Reorganise the big mixed groups on the D0C/D0 tabs into per-function groups. |
| `_build_relabel_monitor.py` | Unify all D0/D0C button labels and (re)build the reply-monitor nodes. |
| `_label_targets.py` | Append the actual target (TIM/channel) to each Request-Response button label. |
| `_dup_monitor_timesync.py` | Replace the standalone Monitor & Time-sync tab with self-contained copies on D0/D0C. |
| `_arrange_d0.py` | Fold `reqTopicD0` into the D0C set-global and remove the orphaned D0 set-global. |
| `_tidy_layout.py` | Tidy widget tiling, focused on the Request-Response demo tab. |
| `_build_m5_tab.py` | Rebuild the "M5Core2" flow + dashboard for the current `m5iot/` environment. |
| `_build_m5_reqres.py` | Add per-unit "M5 Req/Res" groups (Temp/Humid/CO2/Gauge) to the Request-Response tab. |
| `_m5_gauges.py` | Give each M5 TIM group a full gauge set (TEMP/HUMID/CO2/GAUGE). |
| `_split_m5_tim.py` | Split each merged M5 group into two titled groups (TIM3 vs TIM4). |
| `_add_tim4.py` | Extend single-transducer M5 services from TIM3-only to also target TIM4. |
| `_drop_tim01.py` | Remove the obsolete pseudo TIM0/TIM1 widgets (those TIMs were dropped from NCAP). |

> Note: `BROKER = "39d5aa93d6951ccb"` seen in some scripts is the Node-RED
> *broker config-node id* (an internal reference), not a credential.

## Raspberry Pi GPIO test snippets

Tiny standalone scripts to bench-test the wiring (require `RPi.GPIO`).

| Script | What it does |
|---|---|
| `tmphmd.py` | Read the DHT11 temperature/humidity sensor. |
| `servo.py` | Sweep an SG90-style servo via GPIO PWM. |
| `buzzer.py` | Play notes on a passive buzzer via GPIO PWM. |
| `swled.py` | Read a push switch (GPIO8, pull-up) and drive an LED. |
| `swsimple.py` | Minimal push-switch read (GPIO8, pull-up). |
| `potsimple.py` | Read a potentiometer via an RC-charge timing approximation. |

## MQTT smoke-test clients

Minimal `paho-mqtt` clients for manual broker checks.

| Script | What it does |
|---|---|
| `pub.py` | Publish a test message to the broker. |
| `sub.py` | Subscribe and print messages from the broker. |

## TEDS / message generators

Helpers for hand-building 1451 TEDS blobs and message fields.

| Script | What it does |
|---|---|
| `chanTEDSgen.py` | Generate channel-TEDS byte blobs from a Python dict (e.g. Temperature). |
| `genSecurityTEDS.py` | Build a Security TEDS via TLV encoding. |
| `genCRCLEN.py` | Prompt for a hex string and append the 1451 CRC + length. |
