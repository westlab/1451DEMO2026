# IEEEP1451.1.6 based Digital Twin

## Simple start:

- Access to:
https://www.west.sd.keio.ac.jp/~west/OUClec.pdf

- You can also download the zip file of whole designs from github.
Press <>Code and select Download ZIP.


## Minimal requirements:

- Nothing. You can design your Node-RED on free cloud service. See the textbook.

- If you have Rasberry-Pi, follow the textbook, and install the Node-RED. (recommended)

---

# IEEE 1451.1.6 NCAP (Python / MQTT)

`NCAP.py` is an IEEE 1451.1.6 NCAP that speaks IEEE 1451.0 network-service
messages over MQTT. It is a modular, asyncio/gmqtt implementation.

## Files

| File | Role |
|------|------|
| `NCAP.py`     | NCAP main (asyncio + gmqtt). Run this. |
| `APP.py`      | Application/client test driver (sends commands, prints replies). |
| `NCAPmsg.py`  | Message codec + **all message templates** (single source of the wire format). Both D0-OP (binary) and C-OP (CSV). |
| `NCAPtbl.py`  | TIM/transducer table and async subscription table. |
| `config.yml`  | Broker, topics, UUIDs, TEDS, timing. |
| `NCAP_legacy.py` | Previous single-file version, kept for reference. |

## Install & run

```bash
pip install gmqtt pyyaml temporenc      # add RPi.GPIO + dht11 on a Pi

python3 NCAP.py -p -v -a                 # pseudo sensors, verbose, announcements (any PC)
python3 NCAP.py -v -a                     # real DHT11 + servo on a Raspberry Pi

# in another terminal, drive it:
python3 APP.py                            # full demo over D0-OP (binary)
python3 APP.py -C                         # same over C-OP (CSV)
python3 APP.py --only read --tim 0 --ch 1 # single action
```

NCAP flags: `-p` pseudo (no GPIO), `-v` verbose debug, `-a` periodic announcements,
`-d` disable D-OP data publishing, `-c <file>` config.

## Implemented services

Discovery (NCAP/TIM/transducer), synchronous read (single / multi-channel /
block), synchronous write, Read TEDS, event notification (subscribe + streaming
notify), NCAP heartbeat, and periodic NCAP/TIM/channel announcements — in both
D0-OP and C-OP encodings.

## Debug output (`-v`)

Tagged, human-readable lines: `[RECV]` `[SEND]` `[SUB]` `[NOTIFY]` `[ANNOUNCE]`
`[SAMPLE]`. UUIDs are shortened to their last 6 hex digits.

## Adding a real sensor as a TIM

Edit two clearly-marked spots in `NCAP.py` (search for
`★★★ 実センサ／アクチュエータの接続ポイント ★★★`) plus `config.yml`:

1. **config.yml** — add `UUIDTIMn`, `NAMETIMn`, and TEDS (`<PREFIX>TEDS`,
   `<PREFIX>BINMETATEDS`, ...). A commented example block is at the end of the file.
2. **`Hardware`** (in NCAP.py) — implement a read method (and/or a write method
   for an actuator), returning a dummy value when `pseudo` is set.
3. **`SENSOR_DEFS`** (in `NCAP._build_sensors()`) — add one row tying the TIM
   UUID/channel to your read/write function and the TEDS prefix.

That single row wires the new sensor into discovery, read, multi-channel read,
write, TEDS, and async notification automatically.

## Notes on conformance

The wire format follows the previously NIST-DT-CHECK-validated encoding where the
published IEEE 1451.1.6 text was internally inconsistent (see the header comment
in `NCAPmsg.py`). `_String` is encoded as NUL-terminated UTF-8 in binary; re-run
NIST-DT-CHECK after changes to confirm byte-level compatibility for your setup.
