#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""
NCAP.py  --  IEEE 1451.1.6 NCAP (MQTT) reference implementation

A modular, asyncio/gmqtt based rewrite of the original single-file NCAP.py.
It keeps the wire format that the previous implementation validated against
NIST-DT-CHECK, and adds the IEEE 1451.1.6 services that were missing:

  * NCAP / TIM / Transducer-channel discovery        (7.2)
  * Synchronous read, single channel                  (7.3.2)   [mandatory]
  * Synchronous read, multiple channels / TIMs        (7.3.3)
  * Synchronous read, block data                      (7.3.4)
  * Synchronous write, single channel                 (7.3.5)
  * Read TEDS                                          (7.4)
  * Event notification (subscribe + streaming notify) (7.5)   <- async
  * Subscribe NCAP heartbeat                           (7.6)   <- async
  * Periodic NCAP / TIM / Transducer announcements     (7.2.2-7.2.4)

Both D0-OP (binary) and C-OP (CSV) encodings are handled, distinguished by the
topic.  Message (de)serialization lives in NCAPmsg.py; the TIM / subscription
tables live in NCAPtbl.py.

The standard text has some internal inconsistencies; where the published spec
and the NIST-validated working code disagreed, the working code wins.  See the
header of NCAPmsg.py for details.

Run:
    python3 NCAP.py            # on a Raspberry Pi with DHT11 + servo
    python3 NCAP.py -p         # pseudo sensors, no hardware (any machine)
    python3 NCAP.py -p -v      # verbose
Requires: pip install gmqtt pyyaml temporenc
"""

import argparse
import asyncio
import json
import signal
import sys
import time
import uuid as uuidlib

import yaml

import gmqtt

import NCAPmsg as M
from NCAPtbl import TimTable, SubscriptionTable

try:
    import temporenc
    _HAS_TEMPORENC = True
except ImportError:
    _HAS_TEMPORENC = False


# --------------------------------------------------------------------- #
#  small helpers
# --------------------------------------------------------------------- #
def norm_uuid(v):
    """Canonical UUID key: lowercase hex, no '0x', no separators."""
    if isinstance(v, (bytes, bytearray)):
        return bytes(v).hex()
    s = str(v)
    if s.startswith(('0x', '0X')):
        s = s[2:]
    return s.replace('_', '').replace(' ', '').lower()


def now_ns():
    return int(time.time() * 1_000_000_000)


def now_epoch_str():
    # 6.4.13: Time as UNIX EPOCH seconds with fractional nanoseconds
    return '%.9f' % time.time()


# --------------------------------------------------------------------- #
#  TEDS helpers (ported from the original NCAP.py so D0 TEDS stays
#  byte-compatible with what NIST-DT-CHECK accepted)
# --------------------------------------------------------------------- #
def hexstr2bin(hex_string):
    import re
    s = re.sub(r'[\s_]', '', hex_string)
    return bytes.fromhex(s)


def teds_checksum(data: bytes) -> bytes:
    checksum = sum(data) & 0xFFFF
    return ((0xFFFF - checksum) & 0xFFFF).to_bytes(2, 'big')


def teds_wrap(teds_body: bytes) -> bytes:
    """Prepend the 4-byte length and append the 1451.0 checksum."""
    teds_length = len(teds_body) + 2          # body + checksum
    full = teds_length.to_bytes(4, 'big') + teds_body
    return full + teds_checksum(full)


CK_TRANS = 273.2   # degC -> K offset (the demo TEDS is K-based)

# ===================================================================== #
#  *** Connection points for real sensors / actuators ***
#
#  When you want to add a real sensor as a TIM, you only need to edit the
#  following 2 places in this file:
#
#    (A) The Hardware class below
#        ... add the read/write implementation (GPIO / I2C / SPI / ADC etc.)
#           as a method. When pseudo=True (-p startup), branch so it returns
#           dummy values.
#
#    (B) The SENSOR_DEFS table inside NCAP._build_sensors()
#        ... just add one line for "which channel of which TIM(UUID) uses
#           which read/write function". TEDS is fetched automatically from
#           the <PREFIX>... keys in config.yml.
#
#  Summary of the steps to add:
#    1. Add UUIDTIMn / NAMETIMn and TEDS (<PREFIX>BINMETATEDS etc.,
#       and <PREFIX>TEDS) to config.yml.
#    2. Implement the read/write methods in Hardware (both real and pseudo).
#    3. Add one line to SENSOR_DEFS.
#  That alone is reflected across discovery / read / write / TEDS / async
#  notification.
# ===================================================================== #


class Hardware:
    """
    Layer that bundles real I/O. When pseudo=True, returns dummy values
    without accessing hardware.
    Add read/write methods for new sensors here.
    """

    def __init__(self, pseudo):
        self.pseudo = pseudo
        self._gpio = None
        self._dht = None
        self._servo = None
        self._dht_cache = (None, None)   # DHT11 gives both temperature and humidity in one read
        # ---- M5Core2 + SCD41 bridge (over WiFi/MQTT) ------------------ #
        #  Independent of GPIO. Even in pseudo, if the real M5 is publishing
        #  telemetry, prefer those real values; return dummy values only when
        #  nothing has been received.
        self._m5 = {}            # deviceId -> {'temp','humid','co2','gauge','online','ts'}
        self._m5_pub = None      # set by NCAP.run(): publish(topic, payload) callable
        self._m5_prefix = ''     # same as above: 'm5iot/' etc.
        if not pseudo:
            # ---- Real hardware (Raspberry Pi) initialization --------- #
            import RPi.GPIO as GPIO
            import dht11
            self._gpio = GPIO
            GPIO.setwarnings(True)
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(4, GPIO.OUT)          # servo: GPIO4
            self._servo = GPIO.PWM(4, 50)
            self._servo.start(0.0)
            self._dht = dht11.DHT11(pin=15)  # DHT11: GPIO15
            print('Hardware: real (DHT11 pin15, servo GPIO4)')
        else:
            import random
            self._random = random
            print('Hardware: pseudo (no GPIO)')

    # ------------------------------------------------------------------ #
    #  Called once per sampling period. Sensors shared across multiple
    #  channels (like DHT11, which yields multiple values in one read)
    #  update their cache here.
    # ------------------------------------------------------------------ #
    def refresh(self):
        if self.pseudo:
            self._dht_cache = (self._random.randrange(100, 300) / 10 + CK_TRANS,
                               self._random.randrange(200, 700) / 10)
            return
        r = self._dht.read()
        if r.is_valid():
            self._dht_cache = (r.temperature + CK_TRANS, r.humidity)
        # On an invalid read, keep the previous value

    # ---- Read methods (return value = sample value, None if unavailable) ---- #
    def temp(self):
        t = self._dht_cache[0]
        return None if t is None else round(t, 1)

    def humid(self):
        h = self._dht_cache[1]
        return None if h is None else round(h, 1)

    # e.g.) Template for adding a new analog sensor:
    # def adc(self, ch):
    #     if self.pseudo:
    #         return self._random.randrange(0, 1024)
    #     return my_adc_library.read(ch)          # real read

    # ---- Write (actuator) methods ----------------------------------- #
    def servo(self, value):
        deg = float(value)
        if self.pseudo:
            print('++++ pseudo servo <-', deg)
            return
        self._servo.ChangeDutyCycle(deg / 25 + 2.4)
        time.sleep(0.4)
        self._servo.ChangeDutyCycle(0.0)

    # ================================================================ #
    #  M5Core2 + SCD41 bridge (WiFi/MQTT)
    #
    #  Contract (the agreement shared between NCAP.py and the M5 firmware):
    #    telemetry  M5  -> NCAP   <prefix><dev>/telemetry  JSON
    #               {"temp":<degC>,"humid":<%>,"co2":<ppm>,"gauge":<0-100>}
    #    gauge cmd  NCAP -> M5     <prefix><dev>/gauge      plaintext number 0-100
    #    status     M5  -> NCAP   <prefix><dev>/status     "online"/"offline"
    #               (retained + LWT; for connection monitoring)
    #  Temperature is received in degC and returned converted to K(+273.2)
    #  to match the TEDS.
    # ================================================================ #
    def m5_set_publisher(self, publish, prefix):
        """Register the NCAP MQTT client for sending gauge commands."""
        self._m5_pub = publish
        self._m5_prefix = prefix

    def m5_ingest(self, deviceId, data):
        """Ingest telemetry JSON (dict) and cache the latest values."""
        d = self._m5.setdefault(deviceId, {})
        for k in ('temp', 'humid', 'co2', 'gauge'):
            if k in data and data[k] is not None:
                try:
                    d[k] = float(data[k])
                except (TypeError, ValueError):
                    pass
        d['online'] = True

    def m5_status(self, deviceId, text):
        """Reflect the status topic (online/offline)."""
        self._m5.setdefault(deviceId, {})['online'] = (str(text).strip().lower() == 'online')

    def _m5_get(self, deviceId, key):
        v = self._m5.get(deviceId, {}).get(key)
        if v is not None:
            return v
        # Return dummy values so the -p demo runs even if the real M5 is not connected
        if self.pseudo:
            base = {'temp': 250, 'humid': 400, 'co2': 6000, 'gauge': 500}[key]
            return (base + self._random.randrange(-30, 30)) / 10.0
        return None

    def m5_temp(self, deviceId):
        t = self._m5_get(deviceId, 'temp')
        return None if t is None else round(t + CK_TRANS, 1)   # degC -> K

    def m5_humid(self, deviceId):
        h = self._m5_get(deviceId, 'humid')
        return None if h is None else round(h, 1)

    def m5_co2(self, deviceId):
        c = self._m5_get(deviceId, 'co2')
        return None if c is None else round(c, 1)

    def m5_gauge_value(self, deviceId):
        g = self._m5_get(deviceId, 'gauge')
        return None if g is None else round(g, 1)

    def m5_gauge(self, deviceId, value):
        """Send the gauge target value (0-100) to the M5 to move the on-screen needle."""
        try:
            v = float(value)
        except (TypeError, ValueError):
            v = 0.0
        v = max(0.0, min(100.0, v))
        self._m5.setdefault(deviceId, {})['gauge'] = v   # optimistic update for read
        if self._m5_pub is not None:
            self._m5_pub('%s%s/gauge' % (self._m5_prefix, deviceId), str(v))
        if self.pseudo:
            print('++++ pseudo M5[%s] gauge <-' % deviceId, v)

    def cleanup(self):
        if self._gpio:
            self._gpio.cleanup()


# --------------------------------------------------------------------- #
#  The NCAP
# --------------------------------------------------------------------- #
class NCAP:
    def __init__(self, conf, args):
        self.c = conf
        self.args = args
        self.verbose = args.verbose
        self.hw = Hardware(args.pseudo)
        self.tims = TimTable()
        self.subs = SubscriptionTable()

        # identifiers
        self.ncapId = norm_uuid(conf['UUIDNCAP'])
        self.ncapName = conf['ncapname']

        # Internal tables (_build_sensors() assembles them from SENSOR_DEFS)
        self.readers = {}     # (timId, channelId) -> read function callable() / None
        self.writers = {}     # (timId, channelId) -> write function callable(value) / None
        self.binteds = {}     # timId -> {tedsAccessCode: hex string(config)}
        self.textteds = {}    # timId -> XML string(config)
        self.values = {}      # (timId, channelId) -> latest sample value
        self.securitytext = conf['SECURITYTEDS']
        self._build_sensors()

        # ---- M5Core2 bridge (WiFi/MQTT) settings ------------------- #
        self.m5_enable = bool(conf.get('m5_enable'))
        self.m5prefix = conf.get('m5_topic_prefix', 'm5iot/')

        # ---- topics (kept identical to the original demo) ---------- #
        spfx = conf['spfx']
        self.t_danno = spfx + conf['tomd0aop']                              # publish announce (D0)
        self.t_canno = spfx + conf['tomcaop']                              # publish announce (C)
        self.t_dop_data = spfx + conf['tomdop'] + conf['loc'] + '/' + conf['ncapname']     # D-OP data
        self.t_cop = spfx + conf['tomcop'] + conf['loc'] + '/' + conf['ncapname']          # subscribe C
        self.t_cop_res = spfx + conf['tomcop'] + conf['locclient'] + '/' + conf['appname']  # publish C reply
        self.t_d0op = spfx + conf['tomd0op'] + conf['loc'] + '/' + conf['ncapname']         # subscribe D0
        self.t_d0op_res = spfx + conf['tomd0op'] + conf['locclient'] + '/' + conf['appname']  # publish D0 reply

        # ---- time synchronization (Clause 9): BR-Sync + RR-Sync ----- #
        self.timesync = bool(conf.get('timesync_enable', True))
        self.timesync_interval = float(conf.get('timesync_interval', 5.0))
        self.t_brs = spfx + 'BRSU/SYN'             # 9.2 BR-Sync broadcast (epoch)
        self.t_rrs_req = spfx + 'RRS/REQ'          # 9.3 RR-Sync request (NCAP subscribes)
        self.t_rrs_res = spfx + 'RRS/%s/RES'       # 9.3 RR-Sync reply (per client LNS)

        self.client = None
        self.loop = None

    # ================================================================ #
    #  * TIM / sensor definition table (to add a real sensor, just add one line here)
    # ================================================================ #
    def _build_sensors(self):
        conf = self.c
        hw = self.hw

        # Each row = one transducer channel:
        #   uuidKey   : UUID key in config.yml (that TIM's identifier UUID)
        #   nameKey   : TIM name key in config.yml
        #   channel   : channelId (for multiple ch on the same TIM, list multiple rows)
        #   reader    : read function callable()->value / None (None for actuators)
        #   writer    : write function callable(value)  / None (None for sensors)
        #   tedsPrefix: TEDS config key prefix (e.g. 'TEMP' -> TEMPBINMETATEDS,
        #               TEMPBINCHANTEDS, TEMPBINNAMETEDS, TEMPBINPHYTEDS, TEMPTEDS)
        #
        # vvv To add a real sensor, add a row to this list vvv
        SENSOR_DEFS = [
            # uuidKey,    nameKey,    ch, reader,    writer,     tedsPrefix
            # Pseudo TIM0 (temp) / TIM1 (humid) / TIM2 (servo) removed: no real
            # hardware is wired. The only real transducers are the M5Core2 units
            # (TIM3/TIM4) added below.

            # e.g.) To add an ADC sensor (TIM3, ch1) separate from GPIO15/servo:
            # ('UUIDTIM3', 'NAMETIM3', 1, lambda: hw.adc(0), None, 'PRESS'),
        ]

        # ---- Add M5Core2 + SCD41 devices (WiFi/MQTT) as TIMs ---------- #
        #  1 device = 1 TIM, 4 channels:
        #    ch1=temperature(K) ch2=humidity(%) ch3=CO2(ppm)  ... sensors
        #    ch4=gauge(0-100)                                  ... read+write actuator
        #  Add as many devices as you like via the m5_devices list in config.yml.
        if conf.get('m5_enable'):
            for dev in conf.get('m5_devices', []):
                did = dev['id']
                tk, nk = dev['tim'], dev['name']
                SENSOR_DEFS += [
                    (tk, nk, 1, (lambda d=did: hw.m5_temp(d)),       None,                            'M5TEMP'),
                    (tk, nk, 2, (lambda d=did: hw.m5_humid(d)),      None,                            'M5HUMID'),
                    (tk, nk, 3, (lambda d=did: hw.m5_co2(d)),        None,                            'M5CO2'),
                    (tk, nk, 4, (lambda d=did: hw.m5_gauge_value(d)), (lambda v, d=did: hw.m5_gauge(d, v)), 'M5GAUGE'),
                ]
        # ^^^ End of editable section. Below is automatic processing (normally untouched) ^^^

        sec_bin = conf['SECURITYBINTEDS']
        for uuidKey, nameKey, ch, reader, writer, pfx in SENSOR_DEFS:
            tid = norm_uuid(conf[uuidKey])
            name = conf[nameKey]
            if not self.tims.findtim(tid):
                self.tims.addtim(tid, name)
            self.tims.addxdcr(tid, ch, 'CH%d' % ch)
            if reader is not None:
                self.readers[(tid, ch)] = reader
            if writer is not None:
                self.writers[(tid, ch)] = writer
            # TEDS is kept per (TIM, channel) (supports multi-channel TIMs).
            # Only take in config keys that exist.
            self.binteds.setdefault((tid, ch), {})
            for code, key in ((1, pfx + 'BINMETATEDS'), (3, pfx + 'BINCHANTEDS'),
                              (12, pfx + 'BINNAMETEDS'), (13, pfx + 'BINPHYTEDS')):
                if key in conf:
                    self.binteds[(tid, ch)][code] = conf[key]
            self.binteds[(tid, ch)][16] = sec_bin      # security TEDS is shared
            if pfx + 'TEDS' in conf:
                self.textteds[(tid, ch)] = conf[pfx + 'TEDS']

    # ----- logging ------------------------------------------------- #
    def log(self, *a):
        if self.verbose:
            print(*a)

    @staticmethod
    def _short(v):
        """Make a value readable: shorten UUIDs / long blobs."""
        if isinstance(v, (bytes, bytearray)):
            h = v.hex()
            return ('..' + h[-6:]) if len(v) >= 8 else h
        s = str(v)
        if len(s) > 18 and all(ch in '0123456789abcdefABCDEFxX' for ch in s):
            return '..' + s[-6:]          # UUID-like hex string
        if len(s) > 48:
            return s[:45] + '...'
        return s

    def _fmt(self, d):
        """One readable line from a message dict (skips header/length)."""
        skip = ('netSvcType', 'netSvcId', 'msgType', 'msgLength')
        parts = []
        for k, v in d.items():
            if k in skip:
                continue
            if isinstance(v, list):
                v = '[' + ':'.join(self._short(x) for x in v) + ']'
            else:
                v = self._short(v)
            parts.append('%s=%s' % (k, v))
        return ' '.join(parts)

    def dbg(self, tag, *a):
        """Tagged, human-readable debug line (shown with -v)."""
        if self.verbose:
            print('[%-9s]' % tag, *a)

    # ----- timestamp value appropriate for the encoding ------------ #
    def _ts(self, opname):
        if opname == 'C':
            return now_epoch_str()
        if _HAS_TEMPORENC:
            return temporenc.packb(_dt_now())
        return now_ns()

    # ================================================================ #
    #  MQTT lifecycle
    # ================================================================ #
    def on_connect(self, client, flags, rc, properties):
        print('[CONNECTED rc=%s]' % rc)
        client.subscribe(self.t_cop, qos=0)
        client.subscribe(self.t_d0op, qos=0)
        print('Subscribed:', self.t_cop, '|', self.t_d0op)
        if self.m5_enable:
            client.subscribe(self.m5prefix + '+/telemetry', qos=0)
            client.subscribe(self.m5prefix + '+/status', qos=0)
            print('Subscribed (M5):', self.m5prefix + '+/telemetry',
                  '|', self.m5prefix + '+/status')
        if self.timesync:
            client.subscribe(self.t_rrs_req, qos=0)               # 9.3 RR-Sync requests
            print('Subscribed (time-sync RR):', self.t_rrs_req)

    def on_disconnect(self, client, packet, exc=None):
        print('[DISCONNECTED]')

    def on_message(self, client, topic, payload, qos, properties):
        try:
            self._dispatch(topic, payload)
        except Exception as e:           # never let a bad message kill the NCAP
            print('on_message error:', repr(e))
        return 0

    # ================================================================ #
    #  Dispatch
    # ================================================================ #
    def _opname(self, topic):
        parts = topic.split('/')
        # _1451.1.6 / <TOM> / ...
        tom = parts[1] + '/' if len(parts) > 1 else ''
        if tom == self.c['tomcop']:
            return 'C'
        if tom == self.c['tomd0op']:
            return 'D0'
        return None

    def _dispatch(self, topic, payload):
        if self.m5_enable and topic.startswith(self.m5prefix):
            return self._handle_m5(topic, payload)
        if self.timesync and topic == self.t_rrs_req:
            return self._handle_rrs(payload)
        op = self._opname(topic)
        if op is None:
            self.log('ignored topic', topic)
            return
        if op == 'C':
            text = payload.decode('utf-8', 'replace') if isinstance(payload, (bytes, bytearray)) else payload
            for line in text.splitlines():            # 6.4.14 multi-message
                line = line.strip()
                if line:
                    self._handle_cop(line)
        else:
            self._handle_d0op(payload if isinstance(payload, (bytes, bytearray)) else payload.encode('latin-1'))

    def _handle_m5(self, topic, payload):
        """M5Core2 bridge receive: ingest <prefix><dev>/telemetry | /status."""
        rest = topic[len(self.m5prefix):]
        deviceId, _, kind = rest.partition('/')
        text = payload.decode('utf-8', 'replace') if isinstance(payload, (bytes, bytearray)) else str(payload)
        if kind == 'telemetry':
            try:
                data = json.loads(text)
            except ValueError:
                self.log('M5 bad telemetry json:', text[:60])
                return
            self.hw.m5_ingest(deviceId, data)
            self.dbg('M5', 'telemetry %s %s' % (deviceId, self._fmt(data) if isinstance(data, dict) else text))
        elif kind == 'status':
            self.hw.m5_status(deviceId, text)
            self.dbg('M5', 'status %s = %s' % (deviceId, text.strip()))

    def _header(self, op, data):
        """Return (netSvcType, netSvcId, msgType)."""
        if op == 'C':
            f = data.split(',')
            return int(f[0]), int(f[1]), int(f[2])
        return data[0], data[1], data[2]

    def _handle_cop(self, line):
        st, sid, mt = self._header('C', line)
        if mt != 1:                       # only commands; ignore replies/our echo
            return
        key = (st, sid, mt)
        if key not in M.COMMANDS:
            self.log('C-OP: unsupported', key)
            return
        name, tpl = M.COMMANDS[key]
        cmd = M.NCAPmsg(tpl, msgtype=1).csfdecode(line)
        self.dbg('RECV C-OP', '%-22s' % name, self._fmt(cmd))
        self._handlers[name](self, 'C', cmd)

    def _handle_d0op(self, data):
        st, sid, mt = self._header('D0', data)
        if mt != 1:
            return
        key = (st, sid, mt)
        if key not in M.COMMANDS:
            self.log('D0-OP: unsupported', key)
            return
        name, tpl = M.COMMANDS[key]
        cmd = M.NCAPmsg(tpl, msgtype=0).decode(data)
        self.dbg('RECV D0-OP', '%-22s' % name, self._fmt(cmd))
        self._handlers[name](self, 'D0', cmd)

    # ----- time sync 9.3 RR-Sync (NTP-style request/reply) -------- #
    def _handle_rrs(self, payload):
        """REQ '<clientLNS>[,<t1>]' -> RES '<t1>,<t2>,<t3>,<serverLNS>' (client computes offset/delay)."""
        t2 = now_epoch_str()                       # server receive time
        text = payload.decode('utf-8', 'replace') if isinstance(payload, (bytes, bytearray)) else str(payload)
        f = text.strip().split(',')
        clientLNS = f[0] if f and f[0] else 'client'
        t1 = f[1] if len(f) > 1 else ''
        serverLNS = self.c['loc'] + '/' + self.ncapName
        t3 = now_epoch_str()                       # server transmit time
        res_topic = self.t_rrs_res % clientLNS
        self.client.publish(res_topic, '%s,%s,%s,%s' % (t1, t2, t3, serverLNS), qos=0)
        print('[RR-Sync  ] req from %s -> %s' % (clientLNS, res_topic))

    # ----- reply publishing ---------------------------------------- #
    def _publish(self, op, tpl, d):
        label = REPLY_NAMES.get((tpl['netSvcType']['const'],
                                 tpl['netSvcId']['const'],
                                 tpl['msgType']['const']), 'reply')
        if op == 'C':
            msg = M.NCAPmsg(tpl, msgtype=1).csfencode(d)
            self.client.publish(self.t_cop_res, msg, qos=0)
            self.dbg('SEND C-OP', '%-22s' % label, '-> %s | %s' % (self.t_cop_res, self._fmt(d)))
        else:
            msg = M.NCAPmsg(tpl, msgtype=0).encode(d)
            self.client.publish(self.t_d0op_res, msg, qos=0)
            self.dbg('SEND D0-OP', '%-22s' % label, '-> %s | %s' % (self.t_d0op_res, self._fmt(d)))

    def _ncapId_for(self, op):
        return self.ncapId if op == 'D0' else '0x' + self.ncapId

    # ================================================================ #
    #  Handlers
    # ================================================================ #
    def _check_ncap(self, cmd):
        return norm_uuid(cmd.get('ncapId', '')) == self.ncapId

    def h_ncap_discovery(self, op, cmd):
        self._publish(op, M.ncap_discovery_rep, {
            'errorCode': 0,
            'appId': cmd['appId'],
            'ncapId': self._ncapId_for(op),
            'ncapName': self.ncapName,
            'addressType': 1,
            'ncapAddress': b'\x00\x00\x00\x00' if op == 'D0' else '0.0.0.0',
        })

    def h_ncap_tim_discovery(self, op, cmd):
        if not self._check_ncap(cmd):
            return self._err_ncap(cmd)
        ids = self.tims.timids()
        names = self.tims.timnames()
        self._publish(op, M.ncap_tim_discovery_rep, {
            'errorCode': 0,
            'appId': cmd['appId'],
            'ncapId': self._ncapId_for(op),
            'numOfTims': len(ids),
            'timIds': [('0x' + i) if op == 'C' else i for i in ids],
            'timNames': names,
        })

    def h_ncap_tim_xdcr_discovery(self, op, cmd):
        if not self._check_ncap(cmd):
            return self._err_ncap(cmd)
        tid = norm_uuid(cmd['timId'])
        chids = self.tims.xdcrids(tid)
        chnames = self.tims.xdcrnames(tid)
        self._publish(op, M.ncap_tim_transducer_discovery_rep, {
            'errorCode': 0 if chids else 1,
            'appId': cmd['appId'],
            'ncapId': self._ncapId_for(op),
            'timId': cmd['timId'],
            'numOfTransducerChannels': len(chids),
            'transducerChannelIds': chids,
            'transducerChannelNames': chnames,
        })

    def _read_value(self, timId, channelId):
        """Return the latest sample value (task_sampling keeps self.values updated)."""
        return self.values.get((timId, channelId))

    def h_sync_read(self, op, cmd):
        if not self._check_ncap(cmd):
            return self._err_ncap(cmd)
        tid = norm_uuid(cmd['timId'])
        ch = int(cmd['channelId'])
        val = self._read_value(tid, ch)
        err = 0 if val is not None else 2
        self._publish(op, M.sync_read_rep, {
            'errorCode': err,
            'appId': cmd['appId'],
            'ncapId': self._ncapId_for(op),
            'timId': cmd['timId'],
            'channelId': ch,
            'transducerSampleData': '' if val is None else str(val),
            'timestamp': self._ts(op),
        })

    def h_sync_read_multi(self, op, cmd):
        if not self._check_ncap(cmd):
            return self._err_ncap(cmd)
        timIds = [norm_uuid(x) for x in cmd['timIds']]
        nch = [int(x) for x in cmd['numOfChannelsOfTIMs']]
        chids = [int(x) for x in cmd['channelIds']]
        samples = []
        idx = 0
        for ti, n in zip(timIds, nch):
            for _ in range(n):
                v = self._read_value(ti, chids[idx]) if idx < len(chids) else None
                samples.append('' if v is None else str(v))
                idx += 1
        self._publish(op, M.sync_read_multi_rep, {
            'errorCode': 0,
            'appId': cmd['appId'],
            'ncapId': self._ncapId_for(op),
            'numOfTIMs': len(timIds),
            'timIds': cmd['timIds'],
            'numOfChannelsOfTIMs': nch,
            'channelIds': chids,
            'transducerSampleDatas': samples,
            'timestamp': self._ts(op),
        })

    def h_sync_read_block(self, op, cmd):
        if not self._check_ncap(cmd):
            return self._err_ncap(cmd)
        timIds = [norm_uuid(x) for x in cmd['timIds']]
        nch = [int(x) for x in cmd['numOfChannelsOfTIMs']]
        chids = [int(x) for x in cmd['channelIds']]
        nsamp = int(cmd['numOfSamples']) if cmd.get('numOfSamples') else 1
        blocks = []
        idx = 0
        for ti, n in zip(timIds, nch):
            for _ in range(n):
                v = self._read_value(ti, chids[idx]) if idx < len(chids) else None
                # a block is numOfSamples values; the demo repeats the latest one
                blocks.append(':'.join('' if v is None else str(v) for _ in range(nsamp)))
                idx += 1
        self._publish(op, M.sync_read_block_rep, {
            'errorCode': 0,
            'appId': cmd['appId'],
            'ncapId': self._ncapId_for(op),
            'numOfTIMs': len(timIds),
            'timIds': cmd['timIds'],
            'numOfChannelsOfTIMs': nch,
            'channelIds': chids,
            'transducerBlockDatas': blocks,
            'endTimestamp': self._ts(op),
        })

    def h_sync_write(self, op, cmd):
        if not self._check_ncap(cmd):
            return self._err_ncap(cmd)
        tid = norm_uuid(cmd['timId'])
        ch = int(cmd['channelId'])
        writer = self.writers.get((tid, ch))     # writer function from SENSOR_DEFS
        err = 0
        if writer is not None:
            try:
                writer(cmd['dataValue'])
            except Exception as e:
                print('write error:', repr(e))
                err = 3
        else:
            err = 2                                # not writable (sensor etc.)
        self._publish(op, M.sync_write_rep, {
            'errorCode': err,
            'appId': cmd['appId'],
            'ncapId': self._ncapId_for(op),
            'timId': cmd['timId'],
            'channelId': int(cmd['channelId']),
        })

    def h_read_teds(self, op, cmd):
        if not self._check_ncap(cmd):
            return self._err_ncap(cmd)
        tid = norm_uuid(cmd['timId'])
        code = int(cmd['tedsAccessCode'])
        ch = int(cmd['channelId']) if cmd.get('channelId') not in (None, '') else 0
        # Kept per (TIM, channel). No ch specified / 0 or an unregistered ch falls back to ch1.
        keyc = (tid, ch) if (tid, ch) in self.binteds else (tid, 1)
        if op == 'C':
            raw = self.securitytext if code == 16 else self.textteds.get(keyc, '')
        else:
            tedsmap = self.binteds.get(keyc, {})
            if code in tedsmap:
                raw = teds_wrap(hexstr2bin(tedsmap[code]))
            else:
                raw = b''
        self._publish(op, M.read_teds_rep, {
            'errorCode': 0 if raw else 4,
            'appId': cmd['appId'],
            'ncapId': self._ncapId_for(op),
            'timId': cmd['timId'],
            'channelId': int(cmd['channelId']) if cmd.get('channelId') not in (None, '') else 0,
            'tedsOffset': int(cmd['tedsOffset']) if cmd.get('tedsOffset') not in (None, '') else 0,
            'rawTEDSBlock': raw,
        })

    def h_event_subscribe(self, op, cmd):
        if not self._check_ncap(cmd):
            return self._err_ncap(cmd)
        tid = norm_uuid(cmd['timId'])
        ch = int(cmd['channelId'])
        interval = max(0.2, _time8_seconds(cmd.get('samplingRate'), default=1.0))
        replyTopic = self.t_cop_res if op == 'C' else self.t_d0op_res
        subId = self.subs.add('event', norm_uuid(cmd['appId']), replyTopic, op,
                              timId=tid, channelId=ch, interval=interval)
        self._publish(op, M.event_subscribe_rep, {
            'errorCode': 0,
            'appId': cmd['appId'],
            'ncapId': self._ncapId_for(op),
            'timId': cmd['timId'],
            'channelId': ch,
            'transducerEventPublisher': self.ncapName,
            'subscriptionId': subId,
        })
        print('[SUB      ] event #%d  tim=..%s ch=%d every %.2fs (%s)'
              % (subId, tid[-6:], ch, interval, op))

    def h_heartbeat_subscribe(self, op, cmd):
        interval = max(0.5, _time8_seconds(cmd.get('timeInterval'), default=5.0))
        replyTopic = self.t_cop_res if op == 'C' else self.t_d0op_res
        subId = self.subs.add('heartbeat', norm_uuid(cmd['appId']), replyTopic, op,
                              interval=interval)
        self._publish(op, M.heartbeat_rep, {
            'errorCode': 0,
            'appId': cmd['appId'],
            'ncapId': self._ncapId_for(op),
            'subscriptionId': subId,
        })
        print('[SUB      ] heartbeat #%d every %.2fs (%s)' % (subId, interval, op))

    def h_event_unsubscribe(self, op, cmd):
        """1451.1.6 (4,3): stop all event/stream subscriptions for this app -> notifications halt."""
        appId = norm_uuid(cmd['appId'])
        removed = [s['subId'] for s in self.subs.all()
                   if s['appId'] == appId and s['kind'] in ('event', 'stream')]
        for sid in removed:
            self.subs.remove(sid)
        self._publish(op, M.event_unsubscribe_rep, {
            'errorCode': 0,
            'appId': cmd['appId'],
            'ncapId': self._ncapId_for(op),
            'subscriptionId': removed[0] if removed else 0,
        })
        print('[UNSUB    ] event app=..%s removed %d sub(s) (%s)'
              % (appId[-6:], len(removed), op))

    def h_heartbeat_unsubscribe(self, op, cmd):
        """1451.1.6 (4,12): stop all heartbeat subscriptions for this app."""
        appId = norm_uuid(cmd['appId'])
        removed = [s['subId'] for s in self.subs.all()
                   if s['appId'] == appId and s['kind'] == 'heartbeat']
        for sid in removed:
            self.subs.remove(sid)
        self._publish(op, M.heartbeat_unsubscribe_rep, {
            'errorCode': 0,
            'appId': cmd['appId'],
            'ncapId': self._ncapId_for(op),
        })
        print('[UNSUB    ] heartbeat app=..%s removed %d sub(s) (%s)'
              % (appId[-6:], len(removed), op))

    # ----- TEDS Query / Write / Update (3,1 / 3,3 / 3,4) ---------- #
    def h_query_teds(self, op, cmd):
        if not self._check_ncap(cmd):
            return self._err_ncap(cmd)
        tid = norm_uuid(cmd['timId'])
        ch = int(cmd['channelId']) if cmd.get('channelId') not in (None, '') else 0
        code = int(cmd['tedsAccessCode'])
        keyc = (tid, ch) if (tid, ch) in self.binteds else (tid, 1)
        hexs = self.binteds.get(keyc, {}).get(code, '')
        size = len(hexs) // 2
        self._publish(op, M.query_teds_rep, {
            'errorCode': 0 if size else 4,
            'appId': cmd['appId'], 'ncapId': self._ncapId_for(op),
            'timId': cmd['timId'], 'channelId': ch,
            'tedsAccessCode': code, 'tedsSize': size,
        })
        print('[TEDS QRY ] tim=..%s ch=%d code=%d size=%d (%s)' % (tid[-6:], ch, code, size, op))

    def _store_teds(self, op, cmd, tpl, label):
        if not self._check_ncap(cmd):
            return self._err_ncap(cmd)
        tid = norm_uuid(cmd['timId'])
        ch = int(cmd['channelId']) if cmd.get('channelId') not in (None, '') else 0
        code = int(cmd['tedsAccessCode'])
        block = cmd.get('rawTEDSBlock')
        if isinstance(block, (bytes, bytearray)):
            raw = bytes(block)
        elif isinstance(block, str) and block:
            try:
                raw = hexstr2bin(block)
            except Exception:
                raw = block.encode('utf-8', 'replace')
        else:
            raw = b''
        self.binteds.setdefault((tid, ch), {})[code] = raw.hex()
        self._publish(op, tpl, {
            'errorCode': 0,
            'appId': cmd['appId'], 'ncapId': self._ncapId_for(op),
            'timId': cmd['timId'], 'channelId': ch, 'tedsAccessCode': code,
        })
        print('[TEDS %s] tim=..%s ch=%d code=%d %d bytes (%s)'
              % (label, tid[-6:], ch, code, len(raw), op))

    def h_write_teds(self, op, cmd):
        return self._store_teds(op, cmd, M.write_teds_rep, 'WR ')

    def h_update_teds(self, op, cmd):
        return self._store_teds(op, cmd, M.update_teds_rep, 'UPD')

    # ----- more sync reads: 2,2 / 2,3 / 2,4 ----------------------- #
    def _block_of(self, tid, ch, n):
        """Demo block: repeat the current reading n times, ';'-joined."""
        val = self._read_value(tid, ch)
        n = max(1, min(int(n or 1), 64))
        s = '' if val is None else str(val)
        return ';'.join([s] * n)

    def h_sync_read_block1(self, op, cmd):
        if not self._check_ncap(cmd):
            return self._err_ncap(cmd)
        tid = norm_uuid(cmd['timId']); ch = int(cmd['channelId'])
        self._publish(op, M.sync_read_block1_rep, {
            'errorCode': 0, 'appId': cmd['appId'], 'ncapId': self._ncapId_for(op),
            'timId': cmd['timId'], 'channelId': ch,
            'transducerBlockData': self._block_of(tid, ch, cmd.get('numOfSamples', 1)),
            'endTimestamp': self._ts(op),
        })

    def h_sync_read_multi1tim(self, op, cmd):
        if not self._check_ncap(cmd):
            return self._err_ncap(cmd)
        tid = norm_uuid(cmd['timId'])
        chans = [int(c) for c in (cmd.get('channelIds') or [])]
        vals = []
        for ch in chans:
            v = self._read_value(tid, ch)
            vals.append('' if v is None else str(v))
        self._publish(op, M.sync_read_multi1tim_rep, {
            'errorCode': 0, 'appId': cmd['appId'], 'ncapId': self._ncapId_for(op),
            'timId': cmd['timId'], 'numOfChannels': len(chans),
            'channelIds': chans, 'transducerSampleDatas': vals, 'timestamp': self._ts(op),
        })

    def h_sync_read_block_multi1tim(self, op, cmd):
        if not self._check_ncap(cmd):
            return self._err_ncap(cmd)
        tid = norm_uuid(cmd['timId'])
        chans = [int(c) for c in (cmd.get('channelIds') or [])]
        n = cmd.get('numOfSamples', 1)
        self._publish(op, M.sync_read_block_multi1tim_rep, {
            'errorCode': 0, 'appId': cmd['appId'], 'ncapId': self._ncapId_for(op),
            'timId': cmd['timId'], 'numOfChannels': len(chans),
            'channelIds': chans, 'transducerBlockDatas': [self._block_of(tid, ch, n) for ch in chans],
            'endTimestamp': self._ts(op),
        })

    # ----- more sync writes: 2,8 / 2,9 / 2,10 / 2,11 / 2,12 ------- #
    def _write_one(self, tid, ch, value):
        writer = self.writers.get((tid, ch))
        if writer is None:
            return 2                       # not writable (sensor / unknown ch)
        try:
            writer(value)
            return 0
        except Exception as e:
            print('write error:', repr(e))
            return 3

    @staticmethod
    def _last_sample(block):
        b = str(block or '')
        return b.split(';')[-1] if b else ''

    def _multi_iter(self, cmd):
        """Yield (tid, ch, flatIndex) over a multi-TIM command's channel layout."""
        tims = [norm_uuid(t) for t in (cmd.get('timIds') or [])]
        counts = [int(c) for c in (cmd.get('numOfChannelsOfTIMs') or [])]
        chans = [int(c) for c in (cmd.get('channelIds') or [])]
        idx = 0
        for ti, tid in enumerate(tims):
            for _ in range(counts[ti] if ti < len(counts) else 0):
                if idx < len(chans):
                    yield tid, chans[idx], idx
                idx += 1

    def h_sync_write_block1(self, op, cmd):           # 2,8
        if not self._check_ncap(cmd):
            return self._err_ncap(cmd)
        tid = norm_uuid(cmd['timId']); ch = int(cmd['channelId'])
        err = self._write_one(tid, ch, self._last_sample(cmd.get('transducerBlockData')))
        self._publish(op, M.sync_write_block1_rep, {
            'errorCode': err, 'appId': cmd['appId'], 'ncapId': self._ncapId_for(op),
            'timId': cmd['timId'], 'channelId': ch})

    def h_sync_write_multi1tim(self, op, cmd):        # 2,9
        if not self._check_ncap(cmd):
            return self._err_ncap(cmd)
        tid = norm_uuid(cmd['timId'])
        chans = [int(c) for c in (cmd.get('channelIds') or [])]
        vals = [str(v) for v in (cmd.get('transducerSampleDatas') or [])]
        err = 0
        for i, ch in enumerate(chans):
            err = err or self._write_one(tid, ch, vals[i] if i < len(vals) else '')
        self._publish(op, M.sync_write_multi1tim_rep, {
            'errorCode': err, 'appId': cmd['appId'], 'ncapId': self._ncapId_for(op),
            'timId': cmd['timId'], 'numOfChannels': len(chans), 'channelIds': chans})

    def h_sync_write_block_multi1tim(self, op, cmd):  # 2,10
        if not self._check_ncap(cmd):
            return self._err_ncap(cmd)
        tid = norm_uuid(cmd['timId'])
        chans = [int(c) for c in (cmd.get('channelIds') or [])]
        blocks = [str(b) for b in (cmd.get('transducerBlockDatas') or [])]
        err = 0
        for i, ch in enumerate(chans):
            err = err or self._write_one(tid, ch, self._last_sample(blocks[i] if i < len(blocks) else ''))
        self._publish(op, M.sync_write_block_multi1tim_rep, {
            'errorCode': err, 'appId': cmd['appId'], 'ncapId': self._ncapId_for(op),
            'timId': cmd['timId'], 'numOfChannels': len(chans), 'channelIds': chans})

    def _multi_write_reply(self, op, cmd, tpl, err):
        self._publish(op, tpl, {
            'errorCode': err, 'appId': cmd['appId'], 'ncapId': self._ncapId_for(op),
            'numOfTIMs': cmd.get('numOfTIMs', 0), 'timIds': cmd.get('timIds', []),
            'numOfChannelsOfTIMs': cmd.get('numOfChannelsOfTIMs', []),
            'channelIds': [int(c) for c in (cmd.get('channelIds') or [])]})

    def h_sync_write_multi(self, op, cmd):            # 2,11
        if not self._check_ncap(cmd):
            return self._err_ncap(cmd)
        vals = [str(v) for v in (cmd.get('transducerSampleDatas') or [])]
        err = 0
        for tid, ch, idx in self._multi_iter(cmd):
            err = err or self._write_one(tid, ch, vals[idx] if idx < len(vals) else '')
        self._multi_write_reply(op, cmd, M.sync_write_multi_rep, err)

    def h_sync_write_block_multi(self, op, cmd):      # 2,12
        if not self._check_ncap(cmd):
            return self._err_ncap(cmd)
        blocks = [str(b) for b in (cmd.get('transducerBlockDatas') or [])]
        err = 0
        for tid, ch, idx in self._multi_iter(cmd):
            err = err or self._write_one(tid, ch, self._last_sample(blocks[idx] if idx < len(blocks) else ''))
        self._multi_write_reply(op, cmd, M.sync_write_block_multi_rep, err)

    # ----- async / callback / stream reads (2,13/15/17/19) -------- #
    def _next_cbk(self):
        self._cbkid = getattr(self, '_cbkid', 0) + 1
        return self._cbkid

    def h_async_read_block1(self, op, cmd):           # 2,13 -> grant + 2,14 callback
        if not self._check_ncap(cmd):
            return self._err_ncap(cmd)
        tid = norm_uuid(cmd['timId']); ch = int(cmd['channelId']); cbk = self._next_cbk()
        ncap = self._ncapId_for(op)
        self._publish(op, M.async_read_block1_rep, {
            'errorCode': 0, 'appId': cmd['appId'], 'ncapId': ncap,
            'timId': cmd['timId'], 'channelId': ch, 'callbackId': cbk})
        self._publish(op, M.async_read_block1_cbk, {
            'errorCode': 0, 'appId': cmd['appId'], 'ncapId': ncap,
            'timId': cmd['timId'], 'channelId': ch,
            'transducerBlockData': self._block_of(tid, ch, cmd.get('numOfSamples', 1)),
            'endTimestamp': self._ts(op), 'callbackId': cbk})

    def h_async_read_stream1(self, op, cmd):          # 2,15 -> grant + register stream sub
        if not self._check_ncap(cmd):
            return self._err_ncap(cmd)
        tid = norm_uuid(cmd['timId']); ch = int(cmd['channelId'])
        interval = max(0.2, _time8_seconds(cmd.get('samplingRate'), default=1.0))
        replyTopic = self.t_cop_res if op == 'C' else self.t_d0op_res
        sid = self.subs.add('stream', norm_uuid(cmd['appId']), replyTopic, op,
                            timId=tid, channelId=ch, interval=interval)
        self._publish(op, M.async_read_stream1_rep, {
            'errorCode': 0, 'appId': cmd['appId'], 'ncapId': self._ncapId_for(op),
            'timId': cmd['timId'], 'channelId': ch, 'callbackId': sid})
        print('[ASTREAM  ] stream #%d tim=..%s ch=%d every %.2fs (%s)' % (sid, tid[-6:], ch, interval, op))

    def h_async_read_block_multi1tim(self, op, cmd):  # 2,17 -> grant + 2,18 callback
        if not self._check_ncap(cmd):
            return self._err_ncap(cmd)
        tid = norm_uuid(cmd['timId'])
        chans = [int(c) for c in (cmd.get('channelIds') or [])]
        cbk = self._next_cbk(); n = cmd.get('numOfSamples', 1); ncap = self._ncapId_for(op)
        self._publish(op, M.async_read_block_multi1tim_rep, {
            'errorCode': 0, 'appId': cmd['appId'], 'ncapId': ncap, 'timId': cmd['timId'],
            'numOfChannels': len(chans), 'channelIds': chans, 'callbackId': cbk})
        self._publish(op, M.async_read_block_multi1tim_cbk, {
            'errorCode': 0, 'appId': cmd['appId'], 'ncapId': ncap, 'timId': cmd['timId'],
            'numOfChannels': len(chans), 'channelIds': chans,
            'transducerBlockDatas': [self._block_of(tid, ch, n) for ch in chans],
            'endTimestamp': self._ts(op), 'callbackId': cbk})

    def h_async_read_block_multi(self, op, cmd):      # 2,19 -> grant + 2,20 callback
        if not self._check_ncap(cmd):
            return self._err_ncap(cmd)
        cbk = self._next_cbk(); n = cmd.get('numOfSamples', 1)
        blocks = [self._block_of(tid, ch, n) for tid, ch, _ in self._multi_iter(cmd)]
        base = {'appId': cmd['appId'], 'ncapId': self._ncapId_for(op),
                'numOfTIMs': cmd.get('numOfTIMs', 0), 'timIds': cmd.get('timIds', []),
                'numOfChannelsOfTIMs': cmd.get('numOfChannelsOfTIMs', []),
                'channelIds': [int(c) for c in (cmd.get('channelIds') or [])]}
        self._publish(op, M.async_read_block_multi_rep, dict(base, errorCode=0, callbackId=cbk))
        self._publish(op, M.async_read_block_multi_cbk,
                      dict(base, errorCode=0, transducerBlockDatas=blocks,
                           endTimestamp=self._ts(op), callbackId=cbk))

    # ----- event: multi-channel / multi-TIM (4,4/4,6/4,7/4,9) ----- #
    def h_event_subscribe_multich(self, op, cmd):     # 4,4
        if not self._check_ncap(cmd):
            return self._err_ncap(cmd)
        interval = max(0.2, _time8_seconds(cmd.get('samplingRate'), default=1.0))
        replyTopic = self.t_cop_res if op == 'C' else self.t_d0op_res
        tid = norm_uuid(cmd['timId']); appId = norm_uuid(cmd['appId'])
        chans = [int(c) for c in (cmd.get('channelIds') or [])]
        first = 0
        for ch in chans:
            sid = self.subs.add('event', appId, replyTopic, op, timId=tid, channelId=ch, interval=interval)
            first = first or sid
        self._publish(op, M.event_subscribe_multich_rep, {
            'errorCode': 0, 'appId': cmd['appId'], 'ncapId': self._ncapId_for(op),
            'timId': cmd['timId'], 'numOfChannels': len(chans), 'channelIds': chans,
            'transducerEventPublisher': self.ncapName, 'subscriptionId': first})
        print('[SUB      ] event multi-ch tim=..%s chs=%s every %.2fs (%s)'
              % (tid[-6:], chans, interval, op))

    def h_event_unsubscribe_multich(self, op, cmd):   # 4,6
        if not self._check_ncap(cmd):
            return self._err_ncap(cmd)
        appId = norm_uuid(cmd['appId']); tid = norm_uuid(cmd['timId'])
        chans = set(int(c) for c in (cmd.get('channelIds') or []))
        removed = [s['subId'] for s in self.subs.all()
                   if s['appId'] == appId and s['kind'] == 'event' and s['timId'] == tid
                   and (not chans or s['channelId'] in chans)]
        for sid in removed:
            self.subs.remove(sid)
        self._publish(op, M.event_unsubscribe_multich_rep, {
            'errorCode': 0, 'appId': cmd['appId'], 'ncapId': self._ncapId_for(op),
            'timId': cmd['timId'], 'subscriptionId': removed[0] if removed else 0})
        print('[UNSUB    ] event multi-ch tim=..%s removed %d (%s)' % (tid[-6:], len(removed), op))

    def h_event_subscribe_multitim(self, op, cmd):    # 4,7
        if not self._check_ncap(cmd):
            return self._err_ncap(cmd)
        interval = max(0.2, _time8_seconds(cmd.get('samplingRate'), default=1.0))
        replyTopic = self.t_cop_res if op == 'C' else self.t_d0op_res
        appId = norm_uuid(cmd['appId'])
        first = 0; total = 0
        for tid, ch, idx in self._multi_iter(cmd):
            sid = self.subs.add('event', appId, replyTopic, op, timId=tid, channelId=ch, interval=interval)
            first = first or sid; total += 1
        self._publish(op, M.event_subscribe_multitim_rep, {
            'errorCode': 0, 'appId': cmd['appId'], 'ncapId': self._ncapId_for(op),
            'numOfTIMs': cmd.get('numOfTIMs', 0), 'timIds': cmd.get('timIds', []),
            'numOfChannelsOfTIMs': cmd.get('numOfChannelsOfTIMs', []),
            'channelIds': [int(c) for c in (cmd.get('channelIds') or [])],
            'transducerEventPublisher': self.ncapName, 'subscriptionId': first})
        print('[SUB      ] event multi-TIM %d sub(s) every %.2fs (%s)' % (total, interval, op))

    def h_event_unsubscribe_multitim(self, op, cmd):  # 4,9
        if not self._check_ncap(cmd):
            return self._err_ncap(cmd)
        appId = norm_uuid(cmd['appId'])
        targets = set((tid, ch) for tid, ch, _ in self._multi_iter(cmd))
        removed = [s['subId'] for s in self.subs.all()
                   if s['appId'] == appId and s['kind'] == 'event'
                   and (s['timId'], s['channelId']) in targets]
        for sid in removed:
            self.subs.remove(sid)
        self._publish(op, M.event_unsubscribe_multitim_rep, {
            'errorCode': 0, 'appId': cmd['appId'], 'ncapId': self._ncapId_for(op),
            'subscriptionId': removed[0] if removed else 0})
        print('[UNSUB    ] event multi-TIM removed %d (%s)' % (len(removed), op))

    # ----- error reply -------------------------------------------- #
    def _err_ncap(self, cmd):
        self.log('ncapId mismatch: got', norm_uuid(cmd.get('ncapId', '')), 'want', self.ncapId)

    # ================================================================ #
    #  Background async tasks
    # ================================================================ #
    async def task_sampling(self):
        """Periodically read all sensor channels and update self.values (+ D-OP data publishing)."""
        period = float(self.c.get('sampling_interval', 3.0))
        while True:
            try:
                # Hardware reads can block, so run them in an executor
                await self.loop.run_in_executor(None, self.hw.refresh)
                summary = []
                for (tid, ch), reader in self.readers.items():
                    val = reader()
                    if val is not None:
                        self.values[(tid, ch)] = val
                        # D-OP (human-readable data publishing): SPFX/D/loc/ncap/<TIMname>/<ch>
                        if not self.args.ddisable:
                            tname = self.tims.findtim(tid)['name']
                            self.client.publish('%s/%s/%d' % (self.t_dop_data, tname, ch), str(val))
                        summary.append('%s:ch%d=%s' % (self.tims.findtim(tid)['name'], ch, val))
                if not self.args.ddisable and summary:
                    self.client.publish(self.t_dop_data + '/TIME', now_epoch_str())
                if summary:
                    self.dbg('SAMPLE', '  '.join(summary))
            except Exception as e:
                print('sampling error:', repr(e))
            await asyncio.sleep(period)

    async def task_notify(self):
        """Stream event notifications and heartbeats to subscribers (7.5/7.6)."""
        next_due = {}
        while True:
            now = time.monotonic()
            for s in self.subs.all():
                if now < next_due.get(s['subId'], 0):
                    continue
                next_due[s['subId']] = now + s['interval']
                op = s['opname']
                try:
                    if s['kind'] == 'event':
                        val = self._read_value(s['timId'], s['channelId'])
                        d = {
                            'errorCode': 0,
                            'appId': '0x' + s['appId'] if op == 'C' else s['appId'],
                            'ncapId': self._ncapId_for(op),
                            'timId': ('0x' + s['timId']) if op == 'C' else s['timId'],
                            'channelId': s['channelId'],
                            'subscriptionId': s['subId'],
                            'transducerSampleData': '' if val is None else str(val),
                            'timestamp': self._ts(op),
                        }
                        msg = M.NCAPmsg(M.event_notify, 1 if op == 'C' else 0).encmsg(d)
                    elif s['kind'] == 'stream':       # 2,16 async stream callback
                        val = self._read_value(s['timId'], s['channelId'])
                        d = {
                            'errorCode': 0,
                            'appId': '0x' + s['appId'] if op == 'C' else s['appId'],
                            'ncapId': self._ncapId_for(op),
                            'timId': ('0x' + s['timId']) if op == 'C' else s['timId'],
                            'channelId': s['channelId'],
                            'transducerSampleData': '' if val is None else str(val),
                            'timestamp': self._ts(op),
                            'callbackId': s['subId'],
                        }
                        msg = M.NCAPmsg(M.async_read_stream1_cbk, 1 if op == 'C' else 0).encmsg(d)
                    else:  # heartbeat
                        d = {
                            'ncapId': self._ncapId_for(op),
                            'subscriptionId': s['subId'],
                            'timestamp': self._ts(op),
                        }
                        msg = M.NCAPmsg(M.heartbeat_notify, 1 if op == 'C' else 0).encmsg(d)
                    self.client.publish(s['replyTopic'], msg, qos=0)
                    self.dbg('NOTIFY', '%s #%d -> %s | %s'
                             % (s['kind'], s['subId'], s['replyTopic'], self._fmt(d)))
                except Exception as e:
                    print('notify error sub#%d:' % s['subId'], repr(e))
            await asyncio.sleep(0.2)

    async def task_timesync(self):
        """9.2 BR-Sync: broadcast UNIX epoch to BRSU/SYN periodically (primary clock)."""
        while True:
            try:
                self.client.publish(self.t_brs, now_epoch_str(), qos=0)
                self.dbg('BR-Sync', 'epoch -> %s' % self.t_brs)
            except Exception as e:
                print('br-sync error:', repr(e))
            await asyncio.sleep(self.timesync_interval)

    async def task_announce(self):
        """Periodic NCAP / TIM / Transducer announcements (7.2.2-7.2.4)."""
        if not self.args.announce:
            return
        n_int = float(self.c.get('NS_NCAPanno_interval', 5.0))
        t_int = float(self.c.get('NS_TIManno_interval', 5.0))
        x_int = float(self.c.get('NS_CHanno_interval', 5.0))
        last_t = last_x = 0.0
        while True:
            now = time.monotonic()
            # NCAP announcement (D0 + C special topics)
            self._announce_ncap()
            if now - last_t >= t_int:
                last_t = now
                self._announce_tims()
            if now - last_x >= x_int:
                last_x = now
                self._announce_xdcrs()
            await asyncio.sleep(n_int)

    def _announce_ncap(self):
        d = {'ncapId': self.ncapId, 'ncapName': self.ncapName,
             'addressType': 1, 'ncapAddress': b'\x00\x00\x00\x00'}
        self.client.publish(self.t_danno, M.NCAPmsg(M.ncap_announcement, 0).encode(d), qos=0)
        dc = dict(d, ncapAddress='0.0.0.0', ncapId='0x' + self.ncapId)
        self.client.publish(self.t_canno, M.NCAPmsg(M.ncap_announcement, 1).csfencode(dc), qos=0)
        self.dbg('ANNOUNCE', 'NCAP  id=..%s name=%s -> %s , %s'
                 % (self.ncapId[-6:], self.ncapName, self.t_danno, self.t_canno))

    def _announce_tims(self):
        for tid in self.tims.timids():
            t = self.tims.findtim(tid)
            d = {'ncapId': self.ncapId, 'timId': tid, 'timName': t['name']}
            self.client.publish(self.t_danno, M.NCAPmsg(M.ncap_tim_announcement, 0).encode(d), qos=0)
        self.dbg('ANNOUNCE', 'TIMs  %s' % ['..%s' % t[-6:] for t in self.tims.timids()])

    def _announce_xdcrs(self):
        for tid in self.tims.timids():
            for x in self.tims.findtim(tid)['xdcrs']:
                d = {'ncapId': self.ncapId, 'timId': tid,
                     'transducerChannelId': int(x['id']), 'transducerChannelName': x['name']}
                self.client.publish(self.t_danno,
                                    M.NCAPmsg(M.ncap_tim_transducer_announcement, 0).encode(d), qos=0)
        self.dbg('ANNOUNCE', 'XDCR channels')

    # ----- 7.2 Departure / Abandonment (publish-only) ------------- #
    def _depart_all(self, abandon=False):
        """Publish TIM-transducer, TIM, and NCAP departures (graceful goodbye)."""
        try:
            for tid in self.tims.timids():
                t = self.tims.findtim(tid)
                for x in t['xdcrs']:
                    self.client.publish(self.t_danno, M.NCAPmsg(M.ncap_tim_transducer_departure, 0).encode(
                        {'ncapId': self.ncapId, 'timId': tid,
                         'transducerChannelId': int(x['id']), 'transducerChannelName': x['name']}), qos=0)
                self.client.publish(self.t_danno, M.NCAPmsg(M.ncap_tim_departure, 0).encode(
                    {'ncapId': self.ncapId, 'timId': tid, 'timName': t['name']}), qos=0)
            tpl = M.ncap_abandonment if abandon else M.ncap_departure
            d = {'ncapId': self.ncapId, 'ncapName': self.ncapName}
            self.client.publish(self.t_danno, M.NCAPmsg(tpl, 0).encode(d), qos=0)
            self.client.publish(self.t_canno, M.NCAPmsg(tpl, 1).csfencode(dict(d, ncapId='0x' + self.ncapId)), qos=0)
            print('[DEPART   ] %s id=..%s -> %s , %s'
                  % ('ABANDON' if abandon else 'departure', self.ncapId[-6:], self.t_danno, self.t_canno))
        except Exception as e:
            print('depart error:', repr(e))

    # ================================================================ #
    #  run
    # ================================================================ #
    async def run(self):
        self.loop = asyncio.get_running_loop()
        cid = uuidlib.UUID(int=uuidlib.getnode()).hex[-12:] + 'ncap'
        self.client = gmqtt.Client(cid)
        if self.c.get('username'):
            self.client.set_auth_credentials(self.c['username'], self.c.get('password'))
            print('AUTH', self.c['username'])
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.on_disconnect = self.on_disconnect
        if self.m5_enable:
            # Pass Hardware the publish handle for sending gauge writes to the M5
            self.hw.m5_set_publisher(self.client.publish, self.m5prefix)

        use_tls = bool(self.c.get('mqtttls'))
        await self.client.connect(self.c['mqtthost'], int(self.c['mqttport']),
                                  ssl=use_tls, keepalive=60)
        print('NCAP up. id=%s name=%s' % (self.ncapId, self.ncapName))
        print('  request  topics:', self.t_cop, '|', self.t_d0op)
        print('  reply    topics:', self.t_cop_res, '|', self.t_d0op_res)

        tasks = [
            asyncio.ensure_future(self.task_sampling()),
            asyncio.ensure_future(self.task_notify()),
            asyncio.ensure_future(self.task_announce()),
        ]
        if self.timesync:
            tasks.append(asyncio.ensure_future(self.task_timesync()))
        await STOP.wait()
        for t in tasks:
            t.cancel()
        if self.args.announce:
            self._depart_all()                  # 7.2 graceful departure
        await self.client.disconnect()
        self.hw.cleanup()


# datetime helper kept out of the hot path
def _dt_now():
    import datetime
    return datetime.datetime.now()


def _time8_seconds(v, default=1.0):
    """Interpret a TimeDuration field as seconds (best effort)."""
    if v is None:
        return default
    try:
        if isinstance(v, (bytes, bytearray)):
            import struct
            return struct.unpack('>Q', bytes(v).ljust(8, b'\x00')[:8])[0] / 1e9 or default
        return float(v) or default
    except (ValueError, TypeError):
        return default


# (netSvcType, netSvcId, msgType) -> readable reply name, for debug output
REPLY_NAMES = {
    (1,  8, 2): 'ncap_discovery_rep',
    (1,  9, 2): 'tim_discovery_rep',
    (1, 10, 2): 'xdcr_discovery_rep',
    (2,  1, 2): 'sync_read_rep',
    (2,  5, 2): 'sync_read_multi_rep',
    (2,  6, 2): 'sync_read_block_rep',
    (2,  7, 2): 'sync_write_rep',
    (3,  2, 2): 'read_teds_rep',
    (4,  1, 2): 'event_subscribe_rep',
    (4,  1, 4): 'event_notify',
    (4, 10, 2): 'heartbeat_rep',
    (4, 10, 4): 'heartbeat_notify',
    (4,  3, 2): 'event_unsubscribe_rep',
    (4, 12, 2): 'heartbeat_unsubscribe_rep',
    (3,  1, 2): 'query_teds_rep',
    (3,  3, 2): 'write_teds_rep',
    (3,  4, 2): 'update_teds_rep',
    (2,  2, 2): 'sync_read_block1_rep',
    (2,  3, 2): 'sync_read_multi1tim_rep',
    (2,  4, 2): 'sync_read_block_multi1tim_rep',
    (2,  8, 2): 'sync_write_block1_rep',
    (2,  9, 2): 'sync_write_multi1tim_rep',
    (2, 10, 2): 'sync_write_block_multi1tim_rep',
    (2, 11, 2): 'sync_write_multi_rep',
    (2, 12, 2): 'sync_write_block_multi_rep',
    (4,  4, 2): 'event_subscribe_multich_rep',
    (4,  6, 2): 'event_unsubscribe_multich_rep',
    (4,  7, 2): 'event_subscribe_multitim_rep',
    (4,  9, 2): 'event_unsubscribe_multitim_rep',
    (2, 13, 2): 'async_read_block1_rep',
    (2, 14, 4): 'async_read_block1_cbk',
    (2, 15, 2): 'async_read_stream1_rep',
    (2, 16, 4): 'async_read_stream1_cbk',
    (2, 17, 2): 'async_read_block_multi1tim_rep',
    (2, 18, 4): 'async_read_block_multi1tim_cbk',
    (2, 19, 2): 'async_read_block_multi_rep',
    (2, 20, 4): 'async_read_block_multi_cbk',
}


# Handler dispatch table (filled after class definition)
NCAP._handlers = {
    'ncap_discovery':          NCAP.h_ncap_discovery,
    'ncap_tim_discovery':      NCAP.h_ncap_tim_discovery,
    'ncap_tim_xdcr_discovery': NCAP.h_ncap_tim_xdcr_discovery,
    'sync_read':               NCAP.h_sync_read,
    'sync_read_multi':         NCAP.h_sync_read_multi,
    'sync_read_block':         NCAP.h_sync_read_block,
    'sync_write':              NCAP.h_sync_write,
    'read_teds':               NCAP.h_read_teds,
    'event_subscribe':         NCAP.h_event_subscribe,
    'heartbeat_subscribe':     NCAP.h_heartbeat_subscribe,
    'event_unsubscribe':       NCAP.h_event_unsubscribe,
    'heartbeat_unsubscribe':   NCAP.h_heartbeat_unsubscribe,
    'query_teds':              NCAP.h_query_teds,
    'write_teds':              NCAP.h_write_teds,
    'update_teds':             NCAP.h_update_teds,
    'sync_read_block1':        NCAP.h_sync_read_block1,
    'sync_read_multi1tim':     NCAP.h_sync_read_multi1tim,
    'sync_read_block_multi1tim': NCAP.h_sync_read_block_multi1tim,
    'sync_write_block1':       NCAP.h_sync_write_block1,
    'sync_write_multi1tim':    NCAP.h_sync_write_multi1tim,
    'sync_write_block_multi1tim': NCAP.h_sync_write_block_multi1tim,
    'sync_write_multi':        NCAP.h_sync_write_multi,
    'sync_write_block_multi':  NCAP.h_sync_write_block_multi,
    'event_subscribe_multich':   NCAP.h_event_subscribe_multich,
    'event_unsubscribe_multich': NCAP.h_event_unsubscribe_multich,
    'event_subscribe_multitim':  NCAP.h_event_subscribe_multitim,
    'event_unsubscribe_multitim': NCAP.h_event_unsubscribe_multitim,
    'async_read_block1':         NCAP.h_async_read_block1,
    'async_read_stream1':        NCAP.h_async_read_stream1,
    'async_read_block_multi1tim': NCAP.h_async_read_block_multi1tim,
    'async_read_block_multi':    NCAP.h_async_read_block_multi,
}


# Created inside the event loop in _amain() so it binds to the running loop
# (important on Python 3.9 where asyncio.run() creates a fresh loop).
STOP = None


def _ask_exit(*_):
    if STOP is not None:
        STOP.set()


def parse_args():
    p = argparse.ArgumentParser(prog='NCAP.py',
                                description='IEEE 1451.1.6 NCAP over MQTT')
    p.add_argument('--version', action='version', version='%(prog)s 1.0 (1451.1.6)')
    p.add_argument('-v', '--verbose', action='store_true', default=False)
    p.add_argument('-p', '--pseudo', action='store_true', default=False,
                   help='pseudo sensors (no GPIO/DHT11/servo)')
    p.add_argument('-q', '--quiet', action='store_true', default=False)
    p.add_argument('-c', '--config', default='./config.yml')
    p.add_argument('-d', '--ddisable', action='store_true', default=False,
                   help='disable D-OP periodic data publishing')
    p.add_argument('-a', '--announce', action='store_true', default=False,
                   help='send periodic NCAP/TIM/XDCR announcements')
    return p.parse_args()


def main():
    if sys.version_info[0] != 3:
        print('Python 3 required'); sys.exit(1)
    args = parse_args()
    with open(args.config) as f:
        conf = yaml.safe_load(f)
    _apply_defaults(conf)

    ncap = NCAP(conf, args)
    try:
        asyncio.run(_amain(ncap))
    except KeyboardInterrupt:
        ncap.hw.cleanup()
    print('bye')


async def _amain(ncap):
    global STOP
    STOP = asyncio.Event()
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, _ask_exit)
        loop.add_signal_handler(signal.SIGTERM, _ask_exit)
    except NotImplementedError:        # e.g. Windows
        pass
    await ncap.run()


def _apply_defaults(conf):
    conf.setdefault('spfx', '_1451.1.6/')
    conf.setdefault('tomdop', 'D/')
    conf.setdefault('tomcop', 'C/')
    conf.setdefault('tomcaop', 'C.A/')
    conf.setdefault('tomd0op', 'D0/')
    conf.setdefault('tomd0aop', 'D0.A/')
    conf.setdefault('loc', 'LOC-NCAP-SERVER')
    conf.setdefault('locclient', 'LOC-NCAP-CLIENT')
    conf.setdefault('appname', conf.get('ncapname', 'ncap0'))
    conf.setdefault('sampling_interval', 3.0)
    conf.setdefault('NS_NCAPanno_interval', 5.0)
    conf.setdefault('NS_TIManno_interval', 5.0)
    conf.setdefault('NS_CHanno_interval', 5.0)
    # normalize TOMs to end with '/'
    for k in ('tomcaop', 'tomd0aop'):
        if conf[k] and not conf[k].endswith('/'):
            conf[k] += '/'


if __name__ == '__main__':
    main()
