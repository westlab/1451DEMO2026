#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""
NCAPtbl.py  --  in-memory tables for the IEEE 1451.1.6 NCAP

This is a cleaned up version of ReferenceModel/NCAPtbl.py.  The original had a
few latent bugs that are fixed here:

  * deletetim/deletexdcr used ``pop(i-1)`` which deletes the *wrong* element.
  * leavexdcr referenced an undefined ``xdcrid`` and ``tblent``.
  * leavetim did not return / stop after removing.

Two tables are provided:

  TimTable          - the TIMs this NCAP exposes and their transducer channels.
  SubscriptionTable - asynchronous subscriptions: event notifications (7.5) and
                      NCAP heartbeats (7.6).  This is what the async / streaming
                      feature is built on.
"""

from pprint import pprint


class TimTable:
    """TIMs hosted by this NCAP and their transducer channels."""

    def __init__(self):
        self.tims = []   # [{'id','name','xdcrs':[{'id','name'}], 'apps':[appId]}]

    # ---- TIM level -------------------------------------------------- #
    def addtim(self, timId, timName):
        if self.findtim(timId):
            print('Warning: timId', timId, 'already exists')
            return
        self.tims.append({'id': timId, 'name': timName, 'xdcrs': [], 'apps': []})

    def findtim(self, timId):
        for t in self.tims:
            if t['id'] == timId:
                return t
        return None

    def deletetim(self, timId):
        for i, t in enumerate(self.tims):
            if t['id'] == timId:
                return self.tims.pop(i)
        print('Warning: timId', timId, 'not found (delete)')
        return None

    # ---- transducer channel level ----------------------------------- #
    def addxdcr(self, timId, xdcrId, xdcrName):
        t = self.findtim(timId)
        if not t:
            print('Warning: timId', timId, 'not found (addxdcr)')
            return
        if self.findxdcr(timId, xdcrId):
            print('Warning: xdcrId', xdcrId, 'already exists')
            return
        t['xdcrs'].append({'id': xdcrId, 'name': xdcrName})

    def findxdcr(self, timId, xdcrId):
        t = self.findtim(timId)
        if not t:
            return None
        for x in t['xdcrs']:
            if x['id'] == xdcrId:
                return x
        return None

    def deletexdcr(self, timId, xdcrId):
        t = self.findtim(timId)
        if not t:
            print('Warning: timId', timId, 'not found (deletexdcr)')
            return None
        for i, x in enumerate(t['xdcrs']):
            if x['id'] == xdcrId:
                return t['xdcrs'].pop(i)
        print('Warning: xdcrId', xdcrId, 'not found (deletexdcr)')
        return None

    # ---- listings used to build discovery replies ------------------- #
    def timids(self):
        return [t['id'] for t in self.tims]

    def timnames(self):
        return [t['name'] for t in self.tims]

    def xdcrids(self, timId):
        t = self.findtim(timId)
        return [x['id'] for x in t['xdcrs']] if t else []

    def xdcrnames(self, timId):
        t = self.findtim(timId)
        return [x['name'] for x in t['xdcrs']] if t else []

    def show(self, label=''):
        if label:
            print(label)
        pprint(self.tims)


class SubscriptionTable:
    """
    Asynchronous subscriptions.

    Each entry:
        {'kind': 'event'|'heartbeat',
         'subId': int,
         'appId': str,           # subscriber UUID (hex string)
         'replyTopic': str,      # where notifications are published
         'timId': str|None,
         'channelId': int|None,
         'interval': float,      # seconds between notifications
         'opname': str}          # 'C' or 'D0' so we know which encoding to use
    """

    def __init__(self):
        self.subs = []
        self._next = 1

    def add(self, kind, appId, replyTopic, opname,
            timId=None, channelId=None, interval=1.0):
        subId = self._next
        self._next += 1
        self.subs.append({
            'kind': kind, 'subId': subId, 'appId': appId,
            'replyTopic': replyTopic, 'opname': opname,
            'timId': timId, 'channelId': channelId, 'interval': float(interval),
        })
        return subId

    def remove(self, subId):
        for i, s in enumerate(self.subs):
            if s['subId'] == subId:
                return self.subs.pop(i)
        return None

    def remove_app(self, appId):
        before = len(self.subs)
        self.subs = [s for s in self.subs if s['appId'] != appId]
        return before - len(self.subs)

    def by_kind(self, kind):
        return [s for s in self.subs if s['kind'] == kind]

    def all(self):
        return list(self.subs)


if __name__ == '__main__':
    tt = TimTable()
    tt.addtim('TIM0', 'TEMP')
    tt.addtim('TIM1', 'HUMID')
    tt.addxdcr('TIM0', 1, 'CH0')
    tt.addxdcr('TIM0', 1, 'CH0')          # duplicate -> warning
    tt.deletetim('TIM1')
    tt.show('tims:')
    print('timids:', tt.timids(), 'names:', tt.timnames())

    st = SubscriptionTable()
    a = st.add('event', '0xaa', 'reply/topic', 'D0', timId='TIM0', channelId=1, interval=2.0)
    b = st.add('heartbeat', '0xbb', 'reply/topic2', 'C', interval=5.0)
    print('subs:', st.all())
    st.remove(a)
    print('after remove:', st.all())
    print('OK')
