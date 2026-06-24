#!/usr/bin/env python3
# (1) Unify all D0/D0C button labels to "<D0|D0C> <operation> (detail/code)".
# (2) Add a wide message-monitor window decoding sent/received messages.
import json, urllib.request, sys, re
import NCAPmsg as M

RR = "86483328.1e604"; ZC = "0c7a731dc303bfda"; BROKER = "39d5aa93d6951ccb"
req = urllib.request.Request("http://127.0.0.1:1880/flows", headers={"Node-RED-API-Version": "v2"})
j = json.load(urllib.request.urlopen(req, timeout=10))["flows"]
print("base LIVE (%d nodes)" % len(j))
byid = {n["id"]: n for n in j}
groups = {n["id"]: n.get("name") for n in j if n.get("type") == "ui_group"}
ids = {n["id"] for n in j}

def svc_of(b):
    p = b.get("payload", "")
    if p and "," in p and p[:1].isdigit():
        return ",".join(p.split(",")[:3])
    for w in b.get("wires", []) or []:
        for t in w:
            f = byid.get(t, {}).get("func", "")
            m = re.search(r"[`'\"](\d+),(\d+),(\d+)", f)
            if m: return ",".join(m.groups())
            m = re.search(r"\['u8',(\d+)\][^]]*?\['u8',(\d+)\][^]]*?\['u8',(\d+)\]", f, re.S)
            if m: return ",".join(m.groups())
    return "?"

ENC = lambda g: "D0" if ("D0" in (g or "") and "D0C" not in (g or "")) else "D0C"
OP = {
 "1,8":"NCAP discovery (1,8)","1,9":"TIM discovery (1,9)","1,10":"XDCR discovery (1,10)",
 "2,2":"block read 1ch (2,2)","2,3":"sample multi-ch read (2,3)","2,4":"block multi-ch read (2,4)",
 "2,5":"multi-ch read (2,5)","2,6":"block read (2,6)",
 "2,8":"block write (2,8)","2,9":"sample multi-ch write (2,9)","2,10":"block multi-ch write (2,10)",
 "2,11":"multi-TIM write (2,11)","2,12":"block multi-TIM write (2,12)",
 "2,13":"async block read (2,13)","2,15":"async stream read (2,15)",
 "2,17":"async block multi-ch (2,17)","2,19":"async block multi-TIM (2,19)",
 "3,1":"TEDS query (3,1)","3,3":"TEDS write (3,3)","3,4":"TEDS update (3,4)",
 "4,1":"event subscribe (4,1)","4,3":"event unsubscribe (4,3)",
 "4,4":"event sub multi-ch (4,4)","4,6":"event unsub multi-ch (4,6)",
 "4,7":"event sub multi-TIM (4,7)","4,9":"event unsub multi-TIM (4,9)",
 "4,10":"heartbeat subscribe (4,10)","4,12":"heartbeat unsubscribe (4,12)",
}
LEGACY = {
 "TEMP-D0C-REQ":"single read (Temp)","HUMID-D0C-REQ":"single read (Humid)",
 "TEMP D0-REQ":"single read (Temp)","HUMID-D0-REQ":"single read (Humid)",
 "TEMP-TEDS-D0C-REQ":"read TEDS (Temp)","HUMID-TEDS-D0C-REQ":"read TEDS (Humid)",
 "SERVO-TEDS-D0C-REQ":"read TEDS (Servo)","SECURITY-TEDS-D0C-REQ":"read TEDS (Security)",
 "TEMP-TEDS-D0-REQ":"read TEDS (Temp)","HUMID-TEDS-D0-REQ":"read TEDS (Humid)",
 "SERVO-TEDS-D0-REQ":"read TEDS (Servo)","SECURITY-TEDS-D0-REQ":"read TEDS (Security)",
 "Chan TEDS":"read TEDS (Channel)","Meta TEDS":"read TEDS (Meta)",
 "Name TEDS":"read TEDS (Name)","Phy TEDS":"read TEDS (Phys)",
 "D0C NCAP discovery":"NCAP discovery (1,8)","D0C TIM discovery":"TIM discovery (1,9)","D0C XDCR discovery":"XDCR discovery (1,10)",
 "D0C Multi-ch read":"multi-ch read (2,5)","D0C Block read":"block read (2,6)",
 "D0C Event subscribe":"event subscribe (4,1)","D0C Heartbeat subscribe":"heartbeat subscribe (4,10)",
 "D0 NCAP discovery":"NCAP discovery (1,8)","D0 TIM discovery":"TIM discovery (1,9)","D0 XDCR discovery":"XDCR discovery (1,10)",
 "D0 Multi-ch read":"multi-ch read (2,5)","D0 Block read":"block read (2,6)",
 "D0 Event subscribe":"event subscribe (4,1)","D0 Heartbeat subscribe":"heartbeat subscribe (4,10)",
 "Event Unsub (Stop)":"event unsubscribe (4,3)","Heartbeat Stop":"heartbeat unsubscribe (4,12)",
}
SKIP_GROUPS = lambda g: ("M5" in (g or "")) or ((g or "").endswith("Gauge")) or "Time-sync" in (g or "")

changed = 0
for n in j:
    if n.get("type") != "ui_button":
        continue
    g = groups.get(n.get("group"))
    if SKIP_GROUPS(g):
        continue
    old = n.get("label", "")
    op = LEGACY.get(old)
    if not op:
        code2 = ",".join(svc_of(n).split(",")[:2])
        op = OP.get(code2)
    if not op:
        continue
    newl = ENC(g) + " " + op
    if newl != old:
        n["label"] = newl; changed += 1
print("relabelled %d buttons" % changed)

# ---- (2) wide message monitor ----
NAMES = {}
for nm in dir(M):
    o = getattr(M, nm)
    if isinstance(o, dict) and "netSvcType" in o and "msgType" in o:
        try:
            k = "%d,%d,%d" % (o["netSvcType"]["const"], o["netSvcId"]["const"], o["msgType"]["const"])
            NAMES.setdefault(k, nm)
        except KeyError:
            pass
NAMES_JS = json.dumps(NAMES)

_c = [0]
def nid():
    while True:
        _c[0] += 1; cand = ("cb%014x" % _c[0])[:16]
        if cand not in ids:
            ids.add(cand); return cand
new = []; add = lambda x: (new.append(x), x["id"])[1]

gmon = add(dict(id=nid(), type="ui_group", name="Message monitor (sent / received)", tab=RR,
                order=80, disp=True, width="12", collapse=False, className=""))
tmpl = add(dict(id=nid(), type="ui_template", z=ZC, group=gmon, name="msg log", order=1,
                width="12", height="9", format=(
                    '<div style="font-family:monospace;font-size:11px;white-space:pre-wrap;'
                    'line-height:1.35;height:400px;overflow-y:auto;width:100%;text-align:left">'
                    '{{msg.payload}}</div>'),
                storeOutMessages=True, fwdInMessages=True, resendOnRefresh=True,
                templateScope="local", className="", x=1100, y=2600, wires=[[]]))
FMT = ("var p=msg.payload,bin=Buffer.isBuffer(p),h,raw;\n"
       "if(bin){if(p.length<3)return null;h=[p[0],p[1],p[2]];raw='0x'+p.toString('hex');}\n"
       "else{var f=(''+p).split(',');if(f.length<3)return null;h=[parseInt(f[0]),parseInt(f[1]),parseInt(f[2])];raw=''+p;}\n"
       "var NAMES=" + NAMES_JS + ";\n"
       "var nm=NAMES[h.join(',')]||('svc '+h.join(','));\n"
       "var mt=h[2];var dir=mt===1?'REQ \\u2192':(mt===4?'NTF \\u2190':'REP \\u2190');\n"
       "var enc=bin?'D0 ':'D0C';\n"
       "var d=new Date();function z(n){return ('0'+n).slice(-2);}\n"
       "var ts=z(d.getHours())+':'+z(d.getMinutes())+':'+z(d.getSeconds());\n"
       "var line=ts+'  '+enc+'  '+dir+'  '+nm+'\\n            '+raw.substring(0,220);\n"
       "var log=flow.get('msgmon')||[];log.unshift(line);if(log.length>40)log.length=40;flow.set('msgmon',log);\n"
       "msg.payload=log.join('\\n');return msg;")
fmt = add(dict(id=nid(), type="function", z=ZC, name="msg monitor fmt", func=FMT, outputs=1,
               timeout=0, noerr=0, initialize="", finalize="", libs=[], x=860, y=2600, wires=[[tmpl]]))
for nm_, topic, dt in [("mon C", "_1451.1.6/C/PTTEST/ncap0", "auto"),
                       ("mon D0", "_1451.1.6/D0/PTTEST/ncap0", "auto")]:
    add(dict(id=nid(), type="mqtt in", z=ZC, name=nm_, topic=topic, qos="0", datatype=dt,
             broker=BROKER, nl=False, rap=False, rh=0, inputs=0, x=620, y=2600, wires=[[fmt]]))

j.extend(new)
print("added %d monitor nodes" % len(new))
# validate
allids = {n["id"] for n in j}
for n in j:
    for w in n.get("wires", []) or []:
        for t in (w or []):
            assert t in allids, "dangling"
import subprocess, tempfile, os
p = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False)
p.write("var flow={get:function(){return [];},set:function(){}},Buffer={isBuffer:function(){return false;}};function f(msg){\n%s\n}" % FMT); p.close()
r = subprocess.run(["node", "--check", p.name], capture_output=True, text=True); os.unlink(p.name)
assert r.returncode == 0, "fmt syntax: " + r.stderr
print("monitor fmt syntax OK")

json.dump(j, open("NodeRED.json", "w"), ensure_ascii=False, indent=4)
try:
    body = json.dumps({"flows": j, "deploymentType": "full"}).encode()
    dreq = urllib.request.Request("http://127.0.0.1:1880/flows", data=body, method="POST",
        headers={"Content-Type": "application/json", "Node-RED-API-Version": "v2"})
    print("DEPLOY:", urllib.request.urlopen(dreq, timeout=30).status)
except Exception as e:
    print("DEPLOY FAILED:", repr(e)); sys.exit(1)
live2 = json.load(urllib.request.urlopen(req, timeout=10))["flows"]
print("repo==live:", {n['id'] for n in live2} == {n['id'] for n in j}, "| nodes", len(j))
