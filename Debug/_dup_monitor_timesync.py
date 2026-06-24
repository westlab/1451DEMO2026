#!/usr/bin/env python3
# Remove the standalone 'Monitor & Time-sync' tab and put a self-contained
# monitor + time-sync block on BOTH the D0C and the D0 flow tabs (1451 modes).
import json, urllib.request, sys

ZC = "0c7a731dc303bfda"; ZD = "a4c183a95164db51"; RR = "86483328.1e604"
BROKER = "39d5aa93d6951ccb"; SPFX = "_1451.1.6/"
NAMES_JS = open("/tmp/names.json").read()

req = urllib.request.Request("http://127.0.0.1:1880/flows", headers={"Node-RED-API-Version": "v2"})
j = json.load(urllib.request.urlopen(req, timeout=10))["flows"]
print("base LIVE (%d nodes)" % len(j))

# ---- 1) delete the standalone tab + its nodes + the old monitor/time-sync groups ----
shared = [n["id"] for n in j if n.get("type") == "tab" and n.get("label") == "Monitor & Time-sync"]
oldgroups = [n["id"] for n in j if n.get("type") == "ui_group" and n.get("name") in
             ("Message monitor (sent / received)", "Service replies (monitor)",
              "Time-sync svc (9)", "Time-sync svc D0 (9)")]
delset = set(shared) | set(oldgroups)
delset |= {n["id"] for n in j if n.get("z") in shared}          # nodes on the shared tab
delset |= {n["id"] for n in j if n.get("group") in oldgroups}   # widgets in old groups
j = [n for n in j if n["id"] not in delset]
for n in j:
    if "wires" in n:
        n["wires"] = [[t for t in (w or []) if t not in delset] for w in n["wires"]]
print("deleted shared tab + old groups + their nodes:", len(delset))

ids = {n["id"] for n in j}
_c = [0]
def nid():
    while True:
        _c[0] += 1; cand = ("f1%014x" % _c[0])[:16]
        if cand not in ids:
            ids.add(cand); return cand
new = []; add = lambda n: (new.append(n), n["id"])[1]

FMT = ("var p=msg.payload,bin=Buffer.isBuffer(p),h,raw;\n"
       "if(bin){if(p.length<3)return null;h=[p[0],p[1],p[2]];raw='0x'+p.toString('hex');}\n"
       "else{var f=(''+p).split(',');if(f.length<3)return null;h=[parseInt(f[0]),parseInt(f[1]),parseInt(f[2])];raw=''+p;}\n"
       "var NAMES=" + NAMES_JS + ";\n"
       "var nm=NAMES[h.join(',')]||('svc '+h.join(','));var mt=h[2];\n"
       "var dir=mt===1?'REQ \\u2192':(mt===4?'NTF \\u2190':'REP \\u2190');\n"
       "var d=new Date();function z(n){return ('0'+n).slice(-2);}\n"
       "var ts=z(d.getHours())+':'+z(d.getMinutes())+':'+z(d.getSeconds());\n"
       "var line=ts+'  '+dir+'  '+nm+'\\n            '+raw.substring(0,200);\n"
       "var KEY='msgmon_'+context.get('side');var log=flow.get(KEY)||[];log.unshift(line);"
       "if(log.length>40)log.length=40;flow.set(KEY,log);\nmsg.payload=log.join('\\n');return msg;")

def build_side(z, label, reptopic, order):
    g = add(dict(id=nid(), type="ui_group", name="Monitor & Time-sync (%s)" % label, tab=RR,
                 order=order, disp=True, width="12", collapse=True, className=""))
    # message monitor: mqtt-in(reply topic) -> fmt -> template
    tmpl = add(dict(id=nid(), type="ui_template", z=z, group=g, name="msg log %s" % label, order=1,
                    width="12", height="7", format=(
                        '<div style="font-family:monospace;font-size:11px;white-space:pre-wrap;'
                        'line-height:1.35;height:330px;overflow-y:auto;width:100%;text-align:left">'
                        '{{msg.payload}}</div>'),
                    storeOutMessages=True, fwdInMessages=True, resendOnRefresh=True,
                    templateScope="local", className="", x=900, y=200, wires=[[]]))
    fmt = add(dict(id=nid(), type="function", z=z, name="msg monitor fmt %s" % label,
                   func="context.set('side','%s');\n%s" % (label, FMT), outputs=1, timeout=0,
                   noerr=0, initialize="", finalize="", libs=[], x=640, y=200, wires=[[tmpl]]))
    add(dict(id=nid(), type="mqtt in", z=z, name="mon %s" % label, topic=reptopic, qos="0",
             datatype="auto", broker=BROKER, nl=False, rap=False, rh=0, inputs=0, x=400, y=200, wires=[[fmt]]))
    # time sync: RR-Sync request button -> builder -> RRS out
    rrsout = add(dict(id=nid(), type="mqtt out", z=z, name="RRS publish %s" % label, topic="", qos="0",
                      retain="", respTopic="", contentType="", userProps="", correl="", expiry="",
                      broker=BROKER, x=900, y=320, wires=[]))
    rrsb = add(dict(id=nid(), type="function", z=z, name="RR-Sync request %s" % label,
                    func="msg.topic='%sRRS/REQ';\nmsg.payload='PTTEST/ncap0,'+(Date.now()/1000).toFixed(6);\nreturn msg;" % SPFX,
                    outputs=1, timeout=0, noerr=0, initialize="", finalize="", libs=[], x=640, y=320, wires=[[rrsout]]))
    add(dict(id=nid(), type="ui_button", z=z, group=g, order=2, width="6", height="1", passthru=False,
             label="RR-Sync request (9.3)", tooltip="", color="", bgcolor="", className="", icon="",
             payload="", payloadType="str", topic="", topicType="str", x=400, y=320, wires=[[rrsb]]))
    # RR-Sync RES -> calc -> offset text
    off = add(dict(id=nid(), type="ui_text", z=z, group=g, order=3, width="6", height="1", name="",
                   label="RR-Sync offset/delay", format="{{msg.payload}}", layout="row-spread",
                   className="", x=900, y=400, wires=[]))
    calc = add(dict(id=nid(), type="function", z=z, name="RR-Sync calc %s" % label,
                    func=("var t4=Date.now()/1000;var f=(''+msg.payload).split(',');\n"
                          "var t1=parseFloat(f[0]),t2=parseFloat(f[1]),t3=parseFloat(f[2]),srv=f[3]||'?';\n"
                          "var off=((t2-t1)+(t3-t4))/2, dly=(t4-t1)-(t3-t2);\n"
                          "msg.payload='server='+srv+'  offset='+off.toFixed(6)+'s  delay='+dly.toFixed(6)+'s';return msg;"),
                    outputs=1, timeout=0, noerr=0, initialize="", finalize="", libs=[], x=640, y=400, wires=[[off]]))
    add(dict(id=nid(), type="mqtt in", z=z, name="RR-Sync RES %s" % label,
             topic=SPFX + "RRS/PTTEST/ncap0/RES", qos="0", datatype="auto", broker=BROKER,
             nl=False, rap=False, rh=0, inputs=0, x=400, y=400, wires=[[calc]]))
    # BR-Sync SYN -> epoch text
    ep = add(dict(id=nid(), type="ui_text", z=z, group=g, order=4, width="6", height="1", name="",
                  label="BR-Sync epoch", format="{{msg.payload}}", layout="row-spread", className="",
                  x=900, y=460, wires=[]))
    add(dict(id=nid(), type="mqtt in", z=z, name="BR-Sync SYN %s" % label, topic=SPFX + "BRSU/SYN",
             qos="0", datatype="auto", broker=BROKER, nl=False, rap=False, rh=0, inputs=0,
             x=400, y=460, wires=[[ep]]))

build_side(ZC, "D0C", SPFX + "C/PTTEST/ncap0", 90)
build_side(ZD, "D0", SPFX + "D0/PTTEST/ncap0", 91)
j.extend(new)
print("added per-side monitor+time-sync: %d nodes" % len(new))

# validate + syntax + no cross-tab
allids = {n["id"] for n in j}; byid = {n["id"]: n for n in j}
for n in j:
    for w in n.get("wires", []) or []:
        for t in (w or []): assert t in allids, "dangling"
cross = sum(1 for n in j if n.get("z") for w in (n.get("wires") or []) for t in (w or [])
            if byid.get(t, {}).get("z") and byid[t]["z"] != n["z"])
print("cross-tab wires:", cross)
import subprocess, tempfile, os
bad = 0
for n in new:
    if n.get("type") == "function":
        p = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False)
        p.write("var context={get:function(){return '';},set:function(){}},flow={get:function(){return[];},set:function(){}},Buffer={isBuffer:function(){return false;}};function f(msg){\n%s\n}" % n["func"]); p.close()
        r = subprocess.run(["node", "--check", p.name], capture_output=True, text=True); os.unlink(p.name)
        if r.returncode: print("SYNTAX", n["name"], r.stderr.splitlines()[-1]); bad += 1
assert bad == 0 and cross == 0

json.dump(j, open("NodeRED.json", "w"), ensure_ascii=False, indent=4)
body = json.dumps({"flows": j, "deploymentType": "full"}).encode()
dreq = urllib.request.Request("http://127.0.0.1:1880/flows", data=body, method="POST",
    headers={"Content-Type": "application/json", "Node-RED-API-Version": "v2"})
print("DEPLOY:", urllib.request.urlopen(dreq, timeout=30).status, "| nodes", len(j))
# confirm no standalone tab, and both sides have monitor+time-sync
tabsleft = [n.get("label") for n in j if n.get("type") == "tab"]
print("flow tabs:", tabsleft)
