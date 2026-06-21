#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""
APP.py  --  IEEE 1451.1.6 Application (client) test driver

A small companion to NCAP.py.  It is the "IEEE 1451 client" side: it sends
network-service *commands* to an NCAP and prints the *replies* / *notifications*
it gets back.  It is handy for exercising NCAP.py without Node-RED.

It reuses the very same message templates as the NCAP (NCAPmsg.py), so there is
exactly one definition of the wire format for both sides:

    command  = M.NCAPmsg(<template>, 0).encode({...})   # D0-OP (binary)
    client.publish(REQ, command)                        # send to NCAP request topic
    ... on_message ...
    reply    = M.NCAPmsg(<template>, 0).decode(payload) # decode NCAP reply

Topics (config.yml driven, same scheme as NCAP.py):
    REQ : where the NCAP listens for commands   (loc/ncapname)
    RES : where the NCAP publishes replies      (locclient/appname)

Usage:
    python3 APP.py                 # run the full demo sequence (D0-OP)
    python3 APP.py -C              # use C-OP (CSV) instead of D0-OP
    python3 APP.py --only read     # one action: discover|tims|xdcrs|read|write|teds|sub|hb
    python3 APP.py --tim 0 --ch 1  # pick TIM index (0=TEMP,1=HUMID,2=SERVO) and channel
    python3 APP.py -w 80           # value to write for the 'write' action
    python3 APP.py --wait 8        # seconds to keep listening for notifications

Requires: pip install gmqtt pyyaml
"""

import argparse
import asyncio
import uuid as U

import yaml
import gmqtt

import NCAPmsg as M


# replies / notifications this client understands: (type,id,msgType) -> (name, tpl)
REPLY = {
    (1,  8, 2): ('ncap_discovery_rep',        M.ncap_discovery_rep),
    (1,  9, 2): ('tim_discovery_rep',         M.ncap_tim_discovery_rep),
    (1, 10, 2): ('xdcr_discovery_rep',        M.ncap_tim_transducer_discovery_rep),
    (2,  1, 2): ('sync_read_rep',             M.sync_read_rep),
    (2,  5, 2): ('sync_read_multi_rep',       M.sync_read_multi_rep),
    (2,  7, 2): ('sync_write_rep',            M.sync_write_rep),
    (3,  2, 2): ('read_teds_rep',             M.read_teds_rep),
    (4,  1, 2): ('event_subscribe_rep',       M.event_subscribe_rep),
    (4,  1, 4): ('event_notify',              M.event_notify),
    (4, 10, 2): ('heartbeat_rep',             M.heartbeat_rep),
    (4, 10, 4): ('heartbeat_notify',          M.heartbeat_notify),
    (3,  1, 2): ('query_teds_rep',            M.query_teds_rep),
    (3,  3, 2): ('write_teds_rep',            M.write_teds_rep),
    (3,  4, 2): ('update_teds_rep',           M.update_teds_rep),
    (2,  2, 2): ('sync_read_block1_rep',      M.sync_read_block1_rep),
    (2,  3, 2): ('sync_read_multi1tim_rep',   M.sync_read_multi1tim_rep),
    (2,  4, 2): ('sync_read_block_multi1tim_rep', M.sync_read_block_multi1tim_rep),
    (2,  8, 2): ('sync_write_block1_rep',       M.sync_write_block1_rep),
    (2,  9, 2): ('sync_write_multi1tim_rep',    M.sync_write_multi1tim_rep),
    (2, 10, 2): ('sync_write_block_multi1tim_rep', M.sync_write_block_multi1tim_rep),
    (2, 11, 2): ('sync_write_multi_rep',        M.sync_write_multi_rep),
    (2, 12, 2): ('sync_write_block_multi_rep',  M.sync_write_block_multi_rep),
    (4,  4, 2): ('event_subscribe_multich_rep',    M.event_subscribe_multich_rep),
    (4,  6, 2): ('event_unsubscribe_multich_rep',  M.event_unsubscribe_multich_rep),
    (4,  7, 2): ('event_subscribe_multitim_rep',   M.event_subscribe_multitim_rep),
    (4,  9, 2): ('event_unsubscribe_multitim_rep', M.event_unsubscribe_multitim_rep),
    (2, 13, 2): ('async_read_block1_rep',          M.async_read_block1_rep),
    (2, 14, 4): ('async_read_block1_cbk',          M.async_read_block1_cbk),
    (2, 15, 2): ('async_read_stream1_rep',         M.async_read_stream1_rep),
    (2, 16, 4): ('async_read_stream1_cbk',         M.async_read_stream1_cbk),
    (2, 17, 2): ('async_read_block_multi1tim_rep', M.async_read_block_multi1tim_rep),
    (2, 18, 4): ('async_read_block_multi1tim_cbk', M.async_read_block_multi1tim_cbk),
    (2, 19, 2): ('async_read_block_multi_rep',     M.async_read_block_multi_rep),
    (2, 20, 4): ('async_read_block_multi_cbk',     M.async_read_block_multi_cbk),
}


def short(v):
    if isinstance(v, (bytes, bytearray)):
        return '..' + v.hex()[-6:]
    s = str(v)
    if len(s) > 20 and all(c in '0123456789abcdefABCDEFxX' for c in s):
        return '..' + s[-6:]
    return s if len(s) <= 60 else s[:57] + '...'


def fmt(d):
    skip = ('netSvcType', 'netSvcId', 'msgType', 'msgLength')
    out = []
    for k, v in d.items():
        if k in skip:
            continue
        if isinstance(v, list):
            v = '[' + ':'.join(short(x) for x in v) + ']'
        else:
            v = short(v)
        out.append('%s=%s' % (k, v))
    return ' '.join(out)


class App:
    def __init__(self, conf, args):
        self.c = conf
        self.args = args
        self.op = 'C' if args.cop else 'D0'
        self.mt = 1 if self.op == 'C' else 0

        spfx = conf['spfx']
        tom = conf['tomcop'] if self.op == 'C' else conf['tomd0op']
        self.REQ = spfx + tom + conf['loc'] + '/' + conf['ncapname']
        self.RES = spfx + tom + conf['locclient'] + '/' + conf['appname']
        # time sync topics (Clause 9)
        self.clientLNS = conf['locclient'] + '/' + conf['appname']
        self.t_brs = spfx + 'BRSU/SYN'
        self.t_rrs_req = spfx + 'RRS/REQ'
        self.t_rrs_res = spfx + 'RRS/' + self.clientLNS + '/RES'

        self.appId = conf['UUIDAPP0']
        self.ncapId = conf['UUIDNCAP']
        # List the TIMs from the config in order (TIM3/TIM4 = M5 devices are included automatically when present)
        self.tims = []
        for i in range(0, 16):
            key = 'UUIDTIM%d' % i
            if key in conf:
                self.tims.append(conf[key])
        self.notify_count = 0
        self.client = None

    # ---- mqtt callbacks ------------------------------------------- #
    def on_connect(self, c, flags, rc, properties):
        print('[APP connected rc=%s]' % rc)
        c.subscribe(self.RES, qos=0)
        c.subscribe(self.t_brs, qos=0)
        c.subscribe(self.t_rrs_res, qos=0)
        print('[APP subscribed]', self.RES, '| time-sync:', self.t_brs, self.t_rrs_res)

    def on_message(self, c, topic, payload, qos, properties):
        try:
            if topic == self.t_rrs_res:
                self._rrs_reply(payload)
            elif topic == self.t_brs:
                self._brs(payload)
            else:
                self._recv(payload)
        except Exception as e:
            print('[APP] decode error:', repr(e))
        return 0

    # ---- time sync (Clause 9) ------------------------------------- #
    def _brs(self, payload):
        s = payload.decode('utf-8', 'replace') if isinstance(payload, (bytes, bytearray)) else str(payload)
        print('[APP BR-Sync] server epoch =', s.strip())

    def _rrs_reply(self, payload):
        import time
        t4 = time.time()
        s = payload.decode('utf-8', 'replace') if isinstance(payload, (bytes, bytearray)) else str(payload)
        f = s.split(',')
        try:
            t1, t2, t3 = float(f[0]), float(f[1]), float(f[2])
            srv = f[3] if len(f) > 3 else '?'
            offset = ((t2 - t1) + (t3 - t4)) / 2.0
            delay = (t4 - t1) - (t3 - t2)
            print('[APP RR-Sync] server=%s offset=%+.6fs delay=%.6fs' % (srv, offset, delay))
        except (ValueError, IndexError) as e:
            print('[APP RR-Sync] bad reply:', s, repr(e))

    def _recv(self, payload):
        if self.op == 'C':
            text = payload.decode('utf-8', 'replace') if isinstance(payload, (bytes, bytearray)) else payload
            for line in text.splitlines():
                f = line.split(',')
                if len(f) < 3:
                    continue
                key = (int(f[0]), int(f[1]), int(f[2]))
                if key in REPLY and key[2] in (2, 4):
                    name, tpl = REPLY[key]
                    d = M.NCAPmsg(tpl, 1).csfdecode(line)
                    self._show(name, d)
        else:
            if not isinstance(payload, (bytes, bytearray)) or len(payload) < 3:
                return
            key = (payload[0], payload[1], payload[2])
            if key in REPLY and key[2] in (2, 4):
                name, tpl = REPLY[key]
                d = M.NCAPmsg(tpl, 0).decode(payload)
                self._show(name, d)

    def _show(self, name, d):
        if name in ('event_notify', 'heartbeat_notify') or name.endswith('_cbk'):
            self.notify_count += 1
            print('[APP NOTIFY] %-20s %s' % (name, fmt(d)))
        else:
            print('[APP REPLY ] %-20s %s' % (name, fmt(d)))

    # ---- send helpers --------------------------------------------- #
    def send(self, tpl, d, label=''):
        msg = M.NCAPmsg(tpl, self.mt).encmsg(d)
        self.client.publish(self.REQ, msg, qos=0)
        print('[APP SEND  ] %-20s -> %s | %s' % (label, self.REQ, fmt(d)))

    # ---- individual actions --------------------------------------- #
    def act_discover(self):
        self.send(M.ncap_discovery_cmd, {'appId': self.appId}, 'ncap_discovery')

    def act_tims(self):
        self.send(M.ncap_tim_discovery_cmd,
                  {'appId': self.appId, 'ncapId': self.ncapId}, 'tim_discovery')

    def act_xdcrs(self):
        self.send(M.ncap_tim_transducer_discovery_cmd,
                  {'appId': self.appId, 'ncapId': self.ncapId,
                   'timId': self.tims[self.args.tim]}, 'xdcr_discovery')

    def act_read(self):
        self.send(M.sync_read_cmd,
                  {'appId': self.appId, 'ncapId': self.ncapId,
                   'timId': self.tims[self.args.tim], 'channelId': self.args.ch,
                   'samplingMode': 5, 'timeout': 0}, 'sync_read')

    def act_write(self):
        # Default is the servo (TIM index 2). Use --tim to target another actuator such as the gauge.
        widx = self.args.tim if self.args.only == 'write' and self.args.tim else 2
        self.send(M.sync_write_cmd,
                  {'appId': self.appId, 'ncapId': self.ncapId,
                   'timId': self.tims[widx], 'channelId': self.args.ch,
                   'samplingMode': 5, 'dataValue': str(self.args.write), 'timeout': 0},
                  'sync_write')

    def act_teds(self):
        self.send(M.read_teds_cmd,
                  {'appId': self.appId, 'ncapId': self.ncapId,
                   'timId': self.tims[self.args.tim], 'channelId': 0,
                   'tedsAccessCode': 3, 'tedsOffset': 0, 'timeout': 0}, 'read_teds')

    def act_sub(self):
        self.send(M.event_subscribe_cmd,
                  {'appId': self.appId, 'ncapId': self.ncapId,
                   'timId': self.tims[self.args.tim], 'channelId': self.args.ch,
                   'minMaxThreshold': '', 'transducerEventSubscriber': 'app0',
                   'samplingRate': 0, 'timeoutUnsubscribe': 0}, 'event_subscribe')

    def act_hb(self):
        self.send(M.heartbeat_cmd,
                  {'appId': self.appId, 'timeInterval': 0, 'timeoutUnsubscribe': 0},
                  'heartbeat_subscribe')

    # ---- new services (1451.0/1451.1.6 full coverage) ------------- #
    def act_query_teds(self):
        self.send(M.query_teds_cmd,
                  {'appId': self.appId, 'ncapId': self.ncapId,
                   'timId': self.tims[self.args.tim], 'channelId': self.args.ch,
                   'tedsAccessCode': 12, 'timeout': 0}, 'query_teds')

    def act_write_teds(self):
        self.send(M.write_teds_cmd,
                  {'appId': self.appId, 'ncapId': self.ncapId,
                   'timId': self.tims[self.args.tim], 'channelId': self.args.ch,
                   'tedsAccessCode': 60, 'tedsOffset': 0, 'timeout': 0,
                   'rawTEDSBlock': bytes.fromhex('deadbeef')}, 'write_teds')

    def act_update_teds(self):
        self.send(M.update_teds_cmd,
                  {'appId': self.appId, 'ncapId': self.ncapId,
                   'timId': self.tims[self.args.tim], 'channelId': self.args.ch,
                   'tedsAccessCode': 60, 'tedsOffset': 0, 'timeout': 0,
                   'rawTEDSBlock': bytes.fromhex('cafe')}, 'update_teds')

    def act_read_block(self):
        self.send(M.sync_read_block1_cmd,
                  {'appId': self.appId, 'ncapId': self.ncapId,
                   'timId': self.tims[self.args.tim], 'channelId': self.args.ch,
                   'numOfSamples': 4, 'sampleInterval': 0, 'startTime': 0, 'timeout': 0},
                  'read_block')

    def act_read_multi1(self):
        self.send(M.sync_read_multi1tim_cmd,
                  {'appId': self.appId, 'ncapId': self.ncapId,
                   'timId': self.tims[self.args.tim], 'numOfChannels': 3,
                   'channelIds': [1, 2, 3], 'samplingMode': 5, 'timeout': 0}, 'read_multi1')

    def act_read_block_multi1(self):
        self.send(M.sync_read_block_multi1tim_cmd,
                  {'appId': self.appId, 'ncapId': self.ncapId,
                   'timId': self.tims[self.args.tim], 'numOfChannels': 2,
                   'channelIds': [1, 3], 'numOfSamples': 3, 'sampleInterval': 0,
                   'startTime': 0, 'timeout': 0}, 'read_block_multi1')

    def _wtargets(self):
        """M5 gauge TIMs (last two configured) for multi-TIM actuator writes."""
        return self.tims[-2:] if len(self.tims) >= 2 else self.tims

    def act_write_block(self):            # 2,8
        v = self.args.write
        self.send(M.sync_write_block1_cmd,
                  {'appId': self.appId, 'ncapId': self.ncapId,
                   'timId': self.tims[self.args.tim], 'channelId': self.args.ch,
                   'samplingMode': 5, 'transducerBlockData': '%s;%s;%s' % (v, v, v),
                   'timeout': 0}, 'write_block')

    def act_write_multi1(self):           # 2,9
        self.send(M.sync_write_multi1tim_cmd,
                  {'appId': self.appId, 'ncapId': self.ncapId,
                   'timId': self.tims[self.args.tim], 'numOfChannels': 1,
                   'channelIds': [self.args.ch], 'samplingMode': 5,
                   'transducerSampleDatas': [str(self.args.write)], 'timeout': 0}, 'write_multi1')

    def act_write_block_multi1(self):     # 2,10
        v = self.args.write
        self.send(M.sync_write_block_multi1tim_cmd,
                  {'appId': self.appId, 'ncapId': self.ncapId,
                   'timId': self.tims[self.args.tim], 'numOfChannels': 1,
                   'channelIds': [self.args.ch], 'samplingMode': 5,
                   'transducerBlockDatas': ['%s;%s' % (v, v)], 'timeout': 0}, 'write_block_multi1')

    def act_write_multi(self):            # 2,11  (gauge ch4 of the M5 TIMs)
        tids = self._wtargets()
        self.send(M.sync_write_multi_cmd,
                  {'appId': self.appId, 'ncapId': self.ncapId, 'numOfTIMs': len(tids),
                   'timIds': tids, 'numOfChannelsOfTIMs': [1] * len(tids),
                   'channelIds': [4] * len(tids), 'samplingMode': 5,
                   'transducerSampleDatas': [str(self.args.write)] * len(tids), 'timeout': 0}, 'write_multi')

    def act_write_block_multi(self):      # 2,12
        tids = self._wtargets(); v = self.args.write
        self.send(M.sync_write_block_multi_cmd,
                  {'appId': self.appId, 'ncapId': self.ncapId, 'numOfTIMs': len(tids),
                   'timIds': tids, 'numOfChannelsOfTIMs': [1] * len(tids),
                   'channelIds': [4] * len(tids), 'samplingMode': 5,
                   'transducerBlockDatas': ['%s;%s' % (v, v)] * len(tids), 'timeout': 0}, 'write_block_multi')

    def act_sub_multich(self):            # 4,4
        self.send(M.event_subscribe_multich_cmd,
                  {'appId': self.appId, 'ncapId': self.ncapId, 'timId': self.tims[self.args.tim],
                   'numOfChannels': 3, 'channelIds': [1, 2, 3], 'minMaxThreshold': '',
                   'transducerEventSubscriber': 'app0', 'samplingRate': 0,
                   'timeoutUnsubscribe': 0}, 'sub_multich')

    def act_unsub_multich(self):          # 4,6
        self.send(M.event_unsubscribe_multich_cmd,
                  {'appId': self.appId, 'ncapId': self.ncapId, 'timId': self.tims[self.args.tim],
                   'numOfChannels': 3, 'channelIds': [1, 2, 3], 'subscriptionId': 0}, 'unsub_multich')

    def act_sub_multitim(self):           # 4,7
        tids = self._wtargets(); per = [1, 3]
        self.send(M.event_subscribe_multitim_cmd,
                  {'appId': self.appId, 'ncapId': self.ncapId, 'numOfTIMs': len(tids), 'timIds': tids,
                   'numOfChannelsOfTIMs': [len(per)] * len(tids), 'channelIds': per * len(tids),
                   'minMaxThreshold': '', 'transducerEventSubscriber': 'app0',
                   'samplingRate': 0, 'timeoutUnsubscribe': 0}, 'sub_multitim')

    def act_unsub_multitim(self):         # 4,9
        tids = self._wtargets(); per = [1, 3]
        self.send(M.event_unsubscribe_multitim_cmd,
                  {'appId': self.appId, 'ncapId': self.ncapId, 'numOfTIMs': len(tids), 'timIds': tids,
                   'numOfChannelsOfTIMs': [len(per)] * len(tids), 'channelIds': per * len(tids),
                   'subscriptionId': 0}, 'unsub_multitim')

    def act_aread_block(self):            # 2,13 async block read (grant + callback)
        self.send(M.async_read_block1_cmd,
                  {'appId': self.appId, 'ncapId': self.ncapId, 'timId': self.tims[self.args.tim],
                   'channelId': self.args.ch, 'numOfSamples': 4, 'sampleInterval': 0,
                   'startTime': 0, 'timeout': 0}, 'aread_block')

    def act_aread_stream(self):           # 2,15 async stream read (grant + streaming callbacks)
        self.send(M.async_read_stream1_cmd,
                  {'appId': self.appId, 'ncapId': self.ncapId, 'timId': self.tims[self.args.tim],
                   'channelId': self.args.ch, 'samplingMode': 5, 'samplingRate': 0,
                   'timeout': 0}, 'aread_stream')

    def act_aread_block_multi1(self):     # 2,17 async block read, multi-ch 1 TIM
        self.send(M.async_read_block_multi1tim_cmd,
                  {'appId': self.appId, 'ncapId': self.ncapId, 'timId': self.tims[self.args.tim],
                   'numOfChannels': 3, 'channelIds': [1, 2, 3], 'numOfSamples': 3,
                   'sampleInterval': 0, 'startTime': 0, 'timeout': 0}, 'aread_block_multi1')

    def act_aread_block_multi(self):      # 2,19 async block read, multi-ch multi-TIM
        tids = self._wtargets(); per = [1, 3]
        self.send(M.async_read_block_multi_cmd,
                  {'appId': self.appId, 'ncapId': self.ncapId, 'numOfTIMs': len(tids), 'timIds': tids,
                   'numOfChannelsOfTIMs': [len(per)] * len(tids), 'channelIds': per * len(tids),
                   'numOfSamples': 2, 'sampleInterval': 0, 'startTime': 0, 'timeout': 0}, 'aread_block_multi')

    def act_timesync(self):               # 9.3 RR-Sync request (NTP-style)
        import time
        t1 = '%.6f' % time.time()
        self.client.publish(self.t_rrs_req, '%s,%s' % (self.clientLNS, t1), qos=0)
        print('[APP SEND  ] %-20s -> %s | clientLNS=%s t1=%s'
              % ('rrs_req', self.t_rrs_req, self.clientLNS, t1))

    ACTIONS = {'discover': act_discover, 'tims': act_tims, 'xdcrs': act_xdcrs,
               'read': act_read, 'write': act_write, 'teds': act_teds,
               'sub': act_sub, 'hb': act_hb,
               'query_teds': act_query_teds, 'write_teds': act_write_teds,
               'update_teds': act_update_teds, 'read_block': act_read_block,
               'read_multi1': act_read_multi1, 'read_block_multi1': act_read_block_multi1,
               'write_block': act_write_block, 'write_multi1': act_write_multi1,
               'write_block_multi1': act_write_block_multi1, 'write_multi': act_write_multi,
               'write_block_multi': act_write_block_multi,
               'sub_multich': act_sub_multich, 'unsub_multich': act_unsub_multich,
               'sub_multitim': act_sub_multitim, 'unsub_multitim': act_unsub_multitim,
               'aread_block': act_aread_block, 'aread_stream': act_aread_stream,
               'aread_block_multi1': act_aread_block_multi1, 'aread_block_multi': act_aread_block_multi,
               'timesync': act_timesync}

    # ---- run ------------------------------------------------------ #
    async def run(self):
        cid = 'app' + U.uuid4().hex[:8]
        self.client = gmqtt.Client(cid)
        if self.c.get('username'):
            self.client.set_auth_credentials(self.c['username'], self.c.get('password'))
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        await self.client.connect(self.c['mqtthost'], int(self.c['mqttport']),
                                  ssl=bool(self.c.get('mqtttls')), keepalive=60)
        await asyncio.sleep(1.0)
        print('--- APP using %s-OP ---' % self.op)

        if self.args.only:
            self.ACTIONS[self.args.only](self)
        else:
            # full demo sequence
            for step in ('discover', 'tims', 'xdcrs', 'read', 'teds', 'write', 'sub'):
                self.ACTIONS[step](self)
                await asyncio.sleep(1.0)

        print('--- listening %ss for replies/notifications ---' % self.args.wait)
        await asyncio.sleep(self.args.wait)
        await self.client.disconnect()
        print('[APP] notifications received:', self.notify_count)


def parse_args():
    p = argparse.ArgumentParser(prog='APP.py', description='IEEE 1451.1.6 client test driver')
    p.add_argument('-c', '--config', default='./config.yml')
    p.add_argument('-C', '--cop', action='store_true', help='use C-OP (CSV) instead of D0-OP')
    p.add_argument('--only', choices=list(App.ACTIONS.keys()),
                   help='run only one action instead of the full demo')
    p.add_argument('--tim', type=int, default=0, help='TIM index 0=TEMP 1=HUMID 2=SERVO')
    p.add_argument('--ch', type=int, default=1, help='channel id')
    p.add_argument('-w', '--write', default='90', help='value for the write action')
    p.add_argument('--wait', type=float, default=6.0, help='seconds to listen at the end')
    return p.parse_args()


def main():
    args = parse_args()
    with open(args.config) as f:
        conf = yaml.safe_load(f)
    # minimal defaults (mirror NCAP.py so a bare config still works)
    conf.setdefault('spfx', '_1451.1.6/')
    conf.setdefault('tomcop', 'C/')
    conf.setdefault('tomd0op', 'D0/')
    conf.setdefault('loc', 'PTTEST')
    conf.setdefault('locclient', conf.get('loc', 'PTTEST'))
    conf.setdefault('appname', conf.get('ncapname', 'ncap0'))
    asyncio.run(App(conf, args).run())


if __name__ == '__main__':
    main()
