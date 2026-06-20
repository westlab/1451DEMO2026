#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""
NCAPmsg.py  --  IEEE 1451.1.6 message codec (template driven)

This module replaces the hand-written ``binblk_*`` dictionaries and the long
if/elif chains of the original NCAP.py.  Every IEEE 1451.0 network service
message that IEEE 1451.1.6 carries over MQTT is described once as a *template*
(an ordered dict of field-name -> field-spec).  The same template is used to:

  * encode/decode the D0-OP (binary) representation                (encode/decode)
  * encode/decode the C-OP  (CSV / comma separated) representation (csfencode/csfdecode)

Ground truth for the wire format
--------------------------------
The published IEEE Std 1451.1.6-2025 text contains a few internal
inconsistencies (e.g. 7.3.2 lists ``netSvcId = 5`` for the synchronous read in
the header block, while the C-OP example on the same page is ``2,1,1``).  The
*working* NCAP.py in this repository was validated against NIST-DT-CHECK, so for
every message that already worked we keep exactly the (netSvcType, netSvcId,
msgType) triple that NIST accepted.  New messages follow the C-OP examples of
Section 7.  See the ``const=`` values in each template below.

Field data types ('dt')
------------------------
  u8 u16 u32 u64   unsigned integers, BIG ENDIAN on the wire (matches the
                   working NCAP.py which builds e.g. numOfTims as [0x00,0x03]
                   and reads channelId as a big-endian 2-byte value).
  uuid             16 raw bytes (IEEE 1451.0 UUID).
  str              _String.  Binary = UTF-8 + trailing NUL.  CSF = plain text.
  len              UInt16 msgLength, computed automatically (see LEN_ADJUST).
  err              UInt16 errorCode (alias of u16, kept for readability).
  time8            8 raw bytes (TimeDuration / TimeInstance).
  octets           "rest of message" raw bytes (e.g. rawTEDSBlock).  CSF = hex.
  uuidarray        UUID[]      count taken from field named in 'count'
  u16array         UInt16[]    count from 'count', or 'count_sum' (sum of an
                               earlier UInt16Array, used for channelIds)
  strarray         _String[]   count from 'count'

Special spec keys
-----------------
  const : header constant; checked on decode, supplied on encode if omitted.
  count : name of an earlier integer field giving the element count.
  count_sum : name of an earlier u16array; the element count is the sum of it.
"""

import struct

# msgType:    Reserved 0  Command 1  Reply 2  Announcement 3  Notification 4  Callback 5
# netSvcType: Discovery 1  TransducerAccess 2  TEDS 3  EventNotification 4  TransducerManager 5

# The original code wrote msgLength with insert_length() as (total_len - 6),
# big endian, at the byte right after msgType.  We replicate that exactly so the
# D0 wire format stays byte compatible with what NIST-DT-CHECK accepted.
LEN_ADJUST = 6


class NCAPmsg:
    def __init__(self, tpl, msgtype=0, maxbytelength=2048):
        """msgtype: 0 = D0-OP (binary), 1 = C-OP (CSV)."""
        self.tpl = tpl
        self.msgtype = msgtype
        self.maxbytelength = maxbytelength

    # ------------------------------------------------------------------ #
    #  dispatch
    # ------------------------------------------------------------------ #
    def decmsg(self, idata):
        if self.msgtype == 0:
            return self.decode(idata)
        elif self.msgtype == 1:
            return self.csfdecode(idata)
        raise ValueError("Illegal msgtype in decmsg")

    def encmsg(self, idict):
        if self.msgtype == 0:
            return self.encode(idict)
        elif self.msgtype == 1:
            return self.csfencode(idict)
        raise ValueError("Illegal msgtype in encmsg")

    # ------------------------------------------------------------------ #
    #  binary (D0-OP)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _intfmt(dt):
        return {'u8': '>B', 'u16': '>H', 'err': '>H', 'u32': '>I', 'u64': '>Q'}[dt]

    def decode(self, buf):
        if isinstance(buf, str):
            buf = buf.encode('latin-1')
        out = {}
        loc = 0
        for name, spec in self.tpl.items():
            dt = spec['dt']
            if dt in ('u8', 'u16', 'u32', 'u64', 'err', 'len'):
                fmt = '>H' if dt == 'len' else self._intfmt(dt)
                val = struct.unpack_from(fmt, buf, loc)[0]
                loc += struct.calcsize(fmt)
                if 'const' in spec and val != spec['const']:
                    raise ValueError("const mismatch %s: %r != %r" % (name, val, spec['const']))
                out[name] = val
            elif dt == 'uuid':
                out[name] = bytes(buf[loc:loc + 16]); loc += 16
            elif dt == 'time8':
                out[name] = bytes(buf[loc:loc + 8]); loc += 8
            elif dt == 'str':
                end = buf.find(b'\x00', loc)
                if end < 0:
                    end = len(buf)
                out[name] = buf[loc:end].decode('utf-8', 'replace')
                loc = end + 1
            elif dt == 'octets':
                out[name] = bytes(buf[loc:]); loc = len(buf)
            elif dt in ('uuidarray', 'u16array', 'strarray'):
                n = self._arraycount(spec, out)
                arr = []
                for _ in range(n):
                    if dt == 'uuidarray':
                        arr.append(bytes(buf[loc:loc + 16])); loc += 16
                    elif dt == 'u16array':
                        arr.append(struct.unpack_from('>H', buf, loc)[0]); loc += 2
                    else:  # strarray
                        end = buf.find(b'\x00', loc)
                        if end < 0:
                            end = len(buf)
                        arr.append(buf[loc:end].decode('utf-8', 'replace')); loc = end + 1
                out[name] = arr
            else:
                raise ValueError("decode: unknown dt %r in %s" % (dt, name))
        return out

    def encode(self, d):
        buf = bytearray()
        lenloc = None
        for name, spec in self.tpl.items():
            dt = spec['dt']
            if dt == 'len':
                lenloc = len(buf)
                buf += b'\x00\x00'
            elif dt in ('u8', 'u16', 'u32', 'u64', 'err'):
                val = d.get(name, spec.get('const', 0))
                buf += struct.pack(self._intfmt(dt), int(val))
            elif dt == 'uuid':
                buf += self._asuuid(d[name])
            elif dt == 'time8':
                buf += self._astime8(d[name])
            elif dt == 'str':
                buf += self._asbytes(d[name]) + b'\x00'
            elif dt == 'octets':
                buf += self._asbytes(d[name])
            elif dt == 'uuidarray':
                for e in d[name]:
                    buf += self._asuuid(e)
            elif dt == 'u16array':
                for e in d[name]:
                    buf += struct.pack('>H', int(e))
            elif dt == 'strarray':
                for e in d[name]:
                    buf += self._asbytes(e) + b'\x00'
            else:
                raise ValueError("encode: unknown dt %r in %s" % (dt, name))
        if lenloc is not None:
            msglen = max(0, len(buf) - LEN_ADJUST)
            struct.pack_into('>H', buf, lenloc, msglen & 0xFFFF)
        return bytes(buf)

    # ------------------------------------------------------------------ #
    #  CSV (C-OP) -- msgLength is emitted as 0 (6.4.13: length omitted)
    # ------------------------------------------------------------------ #
    def csfencode(self, d):
        cols = []
        for name, spec in self.tpl.items():
            dt = spec['dt']
            if dt == 'len':
                # C-OP omits msgLength entirely (7.1 / 6.4.13). Skip the field.
                continue
            elif dt in ('u8', 'u16', 'u32', 'u64', 'err'):
                cols.append(str(d.get(name, spec.get('const', 0))))
            elif dt == 'uuid':
                cols.append(self._uuidstr(d[name]))
            elif dt == 'octets':
                cols.append(self._hexstr(d[name]))
            elif dt in ('uuidarray', 'u16array', 'strarray'):
                # arrays are colon-separated inside one CSV column (7.3.3)
                if dt == 'uuidarray':
                    cols.append(':'.join(self._uuidstr(e) for e in d[name]))
                else:
                    cols.append(':'.join(str(e) for e in d[name]))
            else:  # str, time8
                cols.append(str(d[name]))
        return ','.join(cols)

    def csfdecode(self, text):
        import csv as _csv
        import io as _io
        row = next(_csv.reader(_io.StringIO(text)))
        out = {}
        i = 0
        for name, spec in self.tpl.items():
            dt = spec['dt']
            if dt == 'len':
                # C-OP carries no msgLength column; do not consume a field.
                out[name] = 0
                continue
            if i >= len(row):
                out[name] = None
                continue
            cell = row[i]
            if dt in ('u8', 'u16', 'u32', 'u64', 'err'):
                try:
                    val = int(cell, 0)
                except ValueError:
                    val = cell
                if 'const' in spec and val != spec['const']:
                    raise ValueError("const mismatch %s: %r != %r" % (name, val, spec['const']))
                out[name] = val
            elif dt in ('uuidarray', 'u16array', 'strarray'):
                out[name] = cell.split(':') if cell else []
            else:
                out[name] = cell
            i += 1
        return out

    # ------------------------------------------------------------------ #
    #  helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _arraycount(spec, out):
        if 'count' in spec:
            return int(out[spec['count']])
        if 'count_sum' in spec:
            return sum(int(x) for x in out[spec['count_sum']])
        raise ValueError("array field needs 'count' or 'count_sum'")

    @staticmethod
    def _asbytes(v):
        if isinstance(v, (bytes, bytearray)):
            return bytes(v)
        return str(v).encode('utf-8')

    @staticmethod
    def _asuuid(v):
        """Accept raw 16 bytes, or a hex string ('0x..' or plain)."""
        if isinstance(v, (bytes, bytearray)):
            return bytes(v).ljust(16, b'\x00')[:16]
        s = str(v)
        if s.startswith(('0x', '0X')):
            s = s[2:]
        s = s.replace('_', '').replace(' ', '').zfill(32)
        return bytes.fromhex(s)[:16].ljust(16, b'\x00')

    @staticmethod
    def _astime8(v):
        if isinstance(v, (bytes, bytearray)):
            return bytes(v).ljust(8, b'\x00')[:8]
        return struct.pack('>Q', int(v) & 0xFFFFFFFFFFFFFFFF)

    @staticmethod
    def _uuidstr(v):
        if isinstance(v, (bytes, bytearray)):
            return '0x' + bytes(v).hex()
        return str(v)

    @staticmethod
    def _hexstr(v):
        if isinstance(v, (bytes, bytearray)):
            return v.hex()
        return str(v)


# ====================================================================== #
#  Message templates
#  triple shown in comments is (netSvcType, netSvcId, msgType)
# ====================================================================== #

# ---- 7.2 Discovery services (netSvcType = 1) ------------------------- #
# Announcements share netSvcId = 1 and differ by msgType (3/4/6).

ncap_announcement = {                                   # 1,1,3
    'netSvcType': {'dt': 'u8',  'const': 1},
    'netSvcId':   {'dt': 'u8',  'const': 1},
    'msgType':    {'dt': 'u8',  'const': 3},
    'msgLength':  {'dt': 'len'},
    'ncapId':     {'dt': 'uuid'},
    'ncapName':   {'dt': 'str'},
    'addressType':{'dt': 'u8'},
    'ncapAddress':{'dt': 'octets'},
}

ncap_tim_announcement = {                               # 1,1,4
    'netSvcType': {'dt': 'u8',  'const': 1},
    'netSvcId':   {'dt': 'u8',  'const': 1},
    'msgType':    {'dt': 'u8',  'const': 4},
    'msgLength':  {'dt': 'len'},
    'ncapId':     {'dt': 'uuid'},
    'timId':      {'dt': 'uuid'},
    'timName':    {'dt': 'str'},
}

ncap_tim_transducer_announcement = {                    # 1,1,6
    'netSvcType': {'dt': 'u8',  'const': 1},
    'netSvcId':   {'dt': 'u8',  'const': 1},
    'msgType':    {'dt': 'u8',  'const': 6},
    'msgLength':  {'dt': 'len'},
    'ncapId':     {'dt': 'uuid'},
    'timId':      {'dt': 'uuid'},
    'transducerChannelId':   {'dt': 'u16'},
    'transducerChannelName': {'dt': 'str'},
}

# Discovery command/reply.  Working NCAP.py ground truth: netSvcId 8/9/10,
# msgType 1 (cmd) / 2 (reply).
ncap_discovery_cmd = {                                  # 1,8,1
    'netSvcType': {'dt': 'u8',  'const': 1},
    'netSvcId':   {'dt': 'u8',  'const': 8},
    'msgType':    {'dt': 'u8',  'const': 1},
    'msgLength':  {'dt': 'len'},
    'appId':      {'dt': 'uuid'},
}

ncap_discovery_rep = {                                  # 1,8,2
    'netSvcType': {'dt': 'u8',  'const': 1},
    'netSvcId':   {'dt': 'u8',  'const': 8},
    'msgType':    {'dt': 'u8',  'const': 2},
    'msgLength':  {'dt': 'len'},
    'errorCode':  {'dt': 'err'},
    'appId':      {'dt': 'uuid'},
    'ncapId':     {'dt': 'uuid'},
    'ncapName':   {'dt': 'str'},
    'addressType':{'dt': 'u8'},
    'ncapAddress':{'dt': 'octets'},
}

ncap_tim_discovery_cmd = {                              # 1,9,1
    'netSvcType': {'dt': 'u8',  'const': 1},
    'netSvcId':   {'dt': 'u8',  'const': 9},
    'msgType':    {'dt': 'u8',  'const': 1},
    'msgLength':  {'dt': 'len'},
    'appId':      {'dt': 'uuid'},
    'ncapId':     {'dt': 'uuid'},
}

ncap_tim_discovery_rep = {                              # 1,9,2
    'netSvcType': {'dt': 'u8',  'const': 1},
    'netSvcId':   {'dt': 'u8',  'const': 9},
    'msgType':    {'dt': 'u8',  'const': 2},
    'msgLength':  {'dt': 'len'},
    'errorCode':  {'dt': 'err'},
    'appId':      {'dt': 'uuid'},
    'ncapId':     {'dt': 'uuid'},
    'numOfTims':  {'dt': 'u16'},
    'timIds':     {'dt': 'uuidarray', 'count': 'numOfTims'},
    'timNames':   {'dt': 'strarray',  'count': 'numOfTims'},
}

ncap_tim_transducer_discovery_cmd = {                   # 1,10,1
    'netSvcType': {'dt': 'u8',  'const': 1},
    'netSvcId':   {'dt': 'u8',  'const': 10},
    'msgType':    {'dt': 'u8',  'const': 1},
    'msgLength':  {'dt': 'len'},
    'appId':      {'dt': 'uuid'},
    'ncapId':     {'dt': 'uuid'},
    'timId':      {'dt': 'uuid'},
}

ncap_tim_transducer_discovery_rep = {                   # 1,10,2
    'netSvcType': {'dt': 'u8',  'const': 1},
    'netSvcId':   {'dt': 'u8',  'const': 10},
    'msgType':    {'dt': 'u8',  'const': 2},
    'msgLength':  {'dt': 'len'},
    'errorCode':  {'dt': 'err'},
    'appId':      {'dt': 'uuid'},
    'ncapId':     {'dt': 'uuid'},
    'timId':      {'dt': 'uuid'},
    'numOfTransducerChannels': {'dt': 'u16'},
    'transducerChannelIds':    {'dt': 'u16array', 'count': 'numOfTransducerChannels'},
    'transducerChannelNames':  {'dt': 'strarray', 'count': 'numOfTransducerChannels'},
}

# ---- 7.3 Transducer access services (netSvcType = 2) ----------------- #

# 7.3.2 single channel read.  Ground truth: 2,1,1 / 2,1,2.
sync_read_cmd = {                                       # 2,1,1
    'netSvcType':  {'dt': 'u8',  'const': 2},
    'netSvcId':    {'dt': 'u8',  'const': 1},
    'msgType':     {'dt': 'u8',  'const': 1},
    'msgLength':   {'dt': 'len'},
    'appId':       {'dt': 'uuid'},
    'ncapId':      {'dt': 'uuid'},
    'timId':       {'dt': 'uuid'},
    'channelId':   {'dt': 'u16'},
    'samplingMode':{'dt': 'u8'},
    'timeout':     {'dt': 'time8'},
}

sync_read_rep = {                                       # 2,1,2
    'netSvcType':  {'dt': 'u8',  'const': 2},
    'netSvcId':    {'dt': 'u8',  'const': 1},
    'msgType':     {'dt': 'u8',  'const': 2},
    'msgLength':   {'dt': 'len'},
    'errorCode':   {'dt': 'err'},
    'appId':       {'dt': 'uuid'},
    'ncapId':      {'dt': 'uuid'},
    'timId':       {'dt': 'uuid'},
    'channelId':   {'dt': 'u16'},
    'transducerSampleData': {'dt': 'str'},
    'timestamp':   {'dt': 'time8'},
}

# 7.3.3 read sample data from multiple channels of multiple TIMs (2,5)
sync_read_multi_cmd = {                                 # 2,5,1
    'netSvcType': {'dt': 'u8',  'const': 2},
    'netSvcId':   {'dt': 'u8',  'const': 5},
    'msgType':    {'dt': 'u8',  'const': 1},
    'msgLength':  {'dt': 'len'},
    'appId':      {'dt': 'uuid'},
    'ncapId':     {'dt': 'uuid'},
    'numOfTIMs':  {'dt': 'u16'},
    'timIds':     {'dt': 'uuidarray', 'count': 'numOfTIMs'},
    'numOfChannelsOfTIMs': {'dt': 'u16array', 'count': 'numOfTIMs'},
    'channelIds': {'dt': 'u16array', 'count_sum': 'numOfChannelsOfTIMs'},
    'samplingMode': {'dt': 'u8'},
    'timeout':    {'dt': 'time8'},
}

sync_read_multi_rep = {                                 # 2,5,2
    'netSvcType': {'dt': 'u8',  'const': 2},
    'netSvcId':   {'dt': 'u8',  'const': 5},
    'msgType':    {'dt': 'u8',  'const': 2},
    'msgLength':  {'dt': 'len'},
    'errorCode':  {'dt': 'err'},
    'appId':      {'dt': 'uuid'},
    'ncapId':     {'dt': 'uuid'},
    'numOfTIMs':  {'dt': 'u16'},
    'timIds':     {'dt': 'uuidarray', 'count': 'numOfTIMs'},
    'numOfChannelsOfTIMs': {'dt': 'u16array', 'count': 'numOfTIMs'},
    'channelIds': {'dt': 'u16array', 'count_sum': 'numOfChannelsOfTIMs'},
    'transducerSampleDatas': {'dt': 'strarray', 'count_sum': 'numOfChannelsOfTIMs'},
    'timestamp':  {'dt': 'time8'},
}

# 7.3.4 read block data from multiple channels of multiple TIMs (2,6)
sync_read_block_cmd = {                                 # 2,6,1
    'netSvcType': {'dt': 'u8',  'const': 2},
    'netSvcId':   {'dt': 'u8',  'const': 6},
    'msgType':    {'dt': 'u8',  'const': 1},
    'msgLength':  {'dt': 'len'},
    'appId':      {'dt': 'uuid'},
    'ncapId':     {'dt': 'uuid'},
    'numOfTIMs':  {'dt': 'u16'},
    'timIds':     {'dt': 'uuidarray', 'count': 'numOfTIMs'},
    'numOfChannelsOfTIMs': {'dt': 'u16array', 'count': 'numOfTIMs'},
    'channelIds': {'dt': 'u16array', 'count_sum': 'numOfChannelsOfTIMs'},
    'numOfSamples':   {'dt': 'u32'},
    'sampleInterval': {'dt': 'time8'},
    'startTime':      {'dt': 'time8'},
    'timeout':        {'dt': 'time8'},
}

sync_read_block_rep = {                                 # 2,6,2
    'netSvcType': {'dt': 'u8',  'const': 2},
    'netSvcId':   {'dt': 'u8',  'const': 6},
    'msgType':    {'dt': 'u8',  'const': 2},
    'msgLength':  {'dt': 'len'},
    'errorCode':  {'dt': 'err'},
    'appId':      {'dt': 'uuid'},
    'ncapId':     {'dt': 'uuid'},
    'numOfTIMs':  {'dt': 'u16'},
    'timIds':     {'dt': 'uuidarray', 'count': 'numOfTIMs'},
    'numOfChannelsOfTIMs': {'dt': 'u16array', 'count': 'numOfTIMs'},
    'channelIds': {'dt': 'u16array', 'count_sum': 'numOfChannelsOfTIMs'},
    'transducerBlockDatas': {'dt': 'strarray', 'count_sum': 'numOfChannelsOfTIMs'},
    'endTimestamp': {'dt': 'time8'},
}

# 7.3.5 write sample data to a channel of a TIM (2,7)
sync_write_cmd = {                                      # 2,7,1
    'netSvcType':  {'dt': 'u8',  'const': 2},
    'netSvcId':    {'dt': 'u8',  'const': 7},
    'msgType':     {'dt': 'u8',  'const': 1},
    'msgLength':   {'dt': 'len'},
    'appId':       {'dt': 'uuid'},
    'ncapId':      {'dt': 'uuid'},
    'timId':       {'dt': 'uuid'},
    'channelId':   {'dt': 'u16'},
    'samplingMode':{'dt': 'u8'},
    'dataValue':   {'dt': 'str'},
    'timeout':     {'dt': 'time8'},
}

sync_write_rep = {                                      # 2,7,2
    'netSvcType':  {'dt': 'u8',  'const': 2},
    'netSvcId':    {'dt': 'u8',  'const': 7},
    'msgType':     {'dt': 'u8',  'const': 2},
    'msgLength':   {'dt': 'len'},
    'errorCode':   {'dt': 'err'},
    'appId':       {'dt': 'uuid'},
    'ncapId':      {'dt': 'uuid'},
    'timId':       {'dt': 'uuid'},
    'channelId':   {'dt': 'u16'},
}

# ---- 7.4 TEDS access services (netSvcType = 3) ----------------------- #
read_teds_cmd = {                                       # 3,2,1
    'netSvcType':     {'dt': 'u8',  'const': 3},
    'netSvcId':       {'dt': 'u8',  'const': 2},
    'msgType':        {'dt': 'u8',  'const': 1},
    'msgLength':      {'dt': 'len'},
    'appId':          {'dt': 'uuid'},
    'ncapId':         {'dt': 'uuid'},
    'timId':          {'dt': 'uuid'},
    'channelId':      {'dt': 'u16'},
    'tedsAccessCode': {'dt': 'u8'},
    'tedsOffset':     {'dt': 'u32'},
    'timeout':        {'dt': 'time8'},
}

read_teds_rep = {                                       # 3,2,2
    'netSvcType':     {'dt': 'u8',  'const': 3},
    'netSvcId':       {'dt': 'u8',  'const': 2},
    'msgType':        {'dt': 'u8',  'const': 2},
    'msgLength':      {'dt': 'len'},
    'errorCode':      {'dt': 'err'},
    'appId':          {'dt': 'uuid'},
    'ncapId':         {'dt': 'uuid'},
    'timId':          {'dt': 'uuid'},
    'channelId':      {'dt': 'u16'},
    'tedsOffset':     {'dt': 'u32'},
    'rawTEDSBlock':   {'dt': 'octets'},
}

# ---- 7.5 Event notification services (netSvcType = 4) --------------- #
event_subscribe_cmd = {                                 # 4,1,1
    'netSvcType': {'dt': 'u8',  'const': 4},
    'netSvcId':   {'dt': 'u8',  'const': 1},
    'msgType':    {'dt': 'u8',  'const': 1},
    'msgLength':  {'dt': 'len'},
    'appId':      {'dt': 'uuid'},
    'ncapId':     {'dt': 'uuid'},
    'timId':      {'dt': 'uuid'},
    'channelId':  {'dt': 'u16'},
    'minMaxThreshold': {'dt': 'str'},
    'transducerEventSubscriber': {'dt': 'str'},
    'samplingRate': {'dt': 'time8'},
    'timeoutUnsubscribe': {'dt': 'time8'},
}

event_subscribe_rep = {                                 # 4,1,2
    'netSvcType': {'dt': 'u8',  'const': 4},
    'netSvcId':   {'dt': 'u8',  'const': 1},
    'msgType':    {'dt': 'u8',  'const': 2},
    'msgLength':  {'dt': 'len'},
    'errorCode':  {'dt': 'err'},
    'appId':      {'dt': 'uuid'},
    'ncapId':     {'dt': 'uuid'},
    'timId':      {'dt': 'uuid'},
    'channelId':  {'dt': 'u16'},
    'transducerEventPublisher': {'dt': 'str'},
    'subscriptionId': {'dt': 'u16'},
}

# NotifyTransducerEvent: continuous notification sent by NCAP to the subscriber.
# Modelled on the sync-read reply so an APP can parse the sample uniformly,
# but with msgType = 4 (Notification).
event_notify = {                                        # 4,1,4
    'netSvcType': {'dt': 'u8',  'const': 4},
    'netSvcId':   {'dt': 'u8',  'const': 1},
    'msgType':    {'dt': 'u8',  'const': 4},
    'msgLength':  {'dt': 'len'},
    'errorCode':  {'dt': 'err'},
    'appId':      {'dt': 'uuid'},
    'ncapId':     {'dt': 'uuid'},
    'timId':      {'dt': 'uuid'},
    'channelId':  {'dt': 'u16'},
    'subscriptionId': {'dt': 'u16'},
    'transducerSampleData': {'dt': 'str'},
    'timestamp':  {'dt': 'time8'},
}

# ---- 7.6 Subscribe NCAP heartbeat (4,10) ---------------------------- #
heartbeat_cmd = {                                       # 4,10,1
    'netSvcType': {'dt': 'u8',  'const': 4},
    'netSvcId':   {'dt': 'u8',  'const': 10},
    'msgType':    {'dt': 'u8',  'const': 1},
    'msgLength':  {'dt': 'len'},
    'appId':      {'dt': 'uuid'},
    'timeInterval':       {'dt': 'time8'},
    'timeoutUnsubscribe': {'dt': 'time8'},
}

heartbeat_rep = {                                       # 4,10,2
    'netSvcType': {'dt': 'u8',  'const': 4},
    'netSvcId':   {'dt': 'u8',  'const': 10},
    'msgType':    {'dt': 'u8',  'const': 2},
    'msgLength':  {'dt': 'len'},
    'errorCode':  {'dt': 'err'},
    'appId':      {'dt': 'uuid'},
    'ncapId':     {'dt': 'uuid'},
    'subscriptionId': {'dt': 'u16'},
}

heartbeat_notify = {                                    # 4,10,4
    'netSvcType': {'dt': 'u8',  'const': 4},
    'netSvcId':   {'dt': 'u8',  'const': 10},
    'msgType':    {'dt': 'u8',  'const': 4},
    'msgLength':  {'dt': 'len'},
    'ncapId':     {'dt': 'uuid'},
    'subscriptionId': {'dt': 'u16'},
    'timestamp':  {'dt': 'time8'},
}

# ---- 7.5 Unsubscribe transducer event (4,3) ------------------------ #
#   1451.1.6-2025: UnsubscribeTransducerEvent... (service code 04 03).
event_unsubscribe_cmd = {                               # 4,3,1
    'netSvcType': {'dt': 'u8',  'const': 4},
    'netSvcId':   {'dt': 'u8',  'const': 3},
    'msgType':    {'dt': 'u8',  'const': 1},
    'msgLength':  {'dt': 'len'},
    'appId':      {'dt': 'uuid'},
    'ncapId':     {'dt': 'uuid'},
    'timId':      {'dt': 'uuid'},
    'channelId':  {'dt': 'u16'},
    'subscriptionId': {'dt': 'u16'},
}

event_unsubscribe_rep = {                               # 4,3,2
    'netSvcType': {'dt': 'u8',  'const': 4},
    'netSvcId':   {'dt': 'u8',  'const': 3},
    'msgType':    {'dt': 'u8',  'const': 2},
    'msgLength':  {'dt': 'len'},
    'errorCode':  {'dt': 'err'},
    'appId':      {'dt': 'uuid'},
    'ncapId':     {'dt': 'uuid'},
    'subscriptionId': {'dt': 'u16'},
}

# ---- 7.6 Unsubscribe NCAP heartbeat (4,12) ------------------------- #
#   1451.1.6-2025: UnsubscribeNCAPHeartbeat (service code 04 12).
heartbeat_unsubscribe_cmd = {                           # 4,12,1
    'netSvcType': {'dt': 'u8',  'const': 4},
    'netSvcId':   {'dt': 'u8',  'const': 12},
    'msgType':    {'dt': 'u8',  'const': 1},
    'msgLength':  {'dt': 'len'},
    'appId':      {'dt': 'uuid'},
}

heartbeat_unsubscribe_rep = {                           # 4,12,2
    'netSvcType': {'dt': 'u8',  'const': 4},
    'netSvcId':   {'dt': 'u8',  'const': 12},
    'msgType':    {'dt': 'u8',  'const': 2},
    'msgLength':  {'dt': 'len'},
    'errorCode':  {'dt': 'err'},
    'appId':      {'dt': 'uuid'},
    'ncapId':     {'dt': 'uuid'},
}

# Lookup table: (netSvcType, netSvcId, msgType) -> (name, template).
# Used by the dispatcher to identify an incoming command quickly.
COMMANDS = {
    (1,  8, 1): ('ncap_discovery',           ncap_discovery_cmd),
    (1,  9, 1): ('ncap_tim_discovery',       ncap_tim_discovery_cmd),
    (1, 10, 1): ('ncap_tim_xdcr_discovery',  ncap_tim_transducer_discovery_cmd),
    (2,  1, 1): ('sync_read',                sync_read_cmd),
    (2,  5, 1): ('sync_read_multi',          sync_read_multi_cmd),
    (2,  6, 1): ('sync_read_block',          sync_read_block_cmd),
    (2,  7, 1): ('sync_write',               sync_write_cmd),
    (3,  2, 1): ('read_teds',                read_teds_cmd),
    (4,  1, 1): ('event_subscribe',          event_subscribe_cmd),
    (4, 10, 1): ('heartbeat_subscribe',      heartbeat_cmd),
    (4,  3, 1): ('event_unsubscribe',        event_unsubscribe_cmd),
    (4, 12, 1): ('heartbeat_unsubscribe',    heartbeat_unsubscribe_cmd),
}


if __name__ == '__main__':
    # round-trip self test (no network / no hardware required)
    print("== NCAPmsg self test ==")
    f = NCAPmsg(sync_read_cmd, msgtype=0)
    sample = {
        'appId':  '0x' + '11' * 16,
        'ncapId': '0x' + '22' * 16,
        'timId':  '0x' + '33' * 16,
        'channelId': 1,
        'samplingMode': 5,
        'timeout': 0,
    }
    b = f.encode(sample)
    print("D0 bytes:", b.hex())
    print("D0 decode:", f.decode(b))

    fc = NCAPmsg(sync_read_cmd, msgtype=1)
    c = fc.csfencode(sample)
    print("C-OP:", c)
    print("C-OP decode:", fc.csfdecode(c))

    fr = NCAPmsg(ncap_tim_discovery_rep, msgtype=0)
    rep = {
        'errorCode': 0,
        'appId':  '0x' + 'aa' * 16,
        'ncapId': '0x' + 'bb' * 16,
        'numOfTims': 2,
        'timIds':   ['0x' + '01' * 16, '0x' + '02' * 16],
        'timNames': ['TEMP', 'HUMID'],
    }
    rb = fr.encode(rep)
    print("TIM disc rep bytes:", rb.hex())
    print("TIM disc rep decode:", fr.decode(rb))
    print("OK")
