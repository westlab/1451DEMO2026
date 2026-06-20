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

        self.appId = conf['UUIDAPP0']
        self.ncapId = conf['UUIDNCAP']
        # 設定にある TIM を順に並べる（TIM3/TIM4 = M5 端末があれば自動で含める）
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
        print('[APP subscribed]', self.RES)

    def on_message(self, c, topic, payload, qos, properties):
        try:
            self._recv(payload)
        except Exception as e:
            print('[APP] decode error:', repr(e))
        return 0

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
        if name in ('event_notify', 'heartbeat_notify'):
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
        # 既定はサーボ(TIM index 2)。--tim でゲージ等 別の actuator を指定可。
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

    ACTIONS = {'discover': act_discover, 'tims': act_tims, 'xdcrs': act_xdcrs,
               'read': act_read, 'write': act_write, 'teds': act_teds,
               'sub': act_sub, 'hb': act_hb}

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
