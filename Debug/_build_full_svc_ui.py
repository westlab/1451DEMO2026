#!/usr/bin/env python3
# Add dashboard buttons for EVERY 1451.0/1451.1.6 service (C-OP/CSV) so all can be
# exercised & verified on screen, plus raw-reply + time-sync monitors.
import json, urllib.request, sys

SRC = "NodeRED.json"
RR = "86483328.1e604"            # ui_tab Request-Response
ZC = "0c7a731dc303bfda"          # D0C flow canvas
OUTC = "00a1510000000019"        # existing empty-topic C publisher (-> msg.topic)
BROKER = "39d5aa93d6951ccb"
SETTINGS = "00a1510000000017"    # D0C settings change node (globals)
T3HEX = "2400250026002700280029003a020f03"
T4HEX = "2400250026002700280029003a020f04"

req = urllib.request.Request("http://127.0.0.1:1880/flows", headers={"Node-RED-API-Version": "v2"})
live = json.load(urllib.request.urlopen(req, timeout=10))["flows"]
repo_text = open(SRC).read(); j = json.loads(repo_text)
only_in_live = {n["id"] for n in live} - {n["id"] for n in j}
if only_in_live:
    print("ABORT: live has nodes not in repo (browser edits would be lost):", only_in_live); sys.exit(1)
print("sync OK: live is subset of repo (%d repo nodes)" % len(j)); ids = {n["id"] for n in j}
byid = {n["id"]: n for n in j}

# ---- 1) add T3/T4 globals to the settings change node ----
sett = byid[SETTINGS]
have = {r.get("p") for r in sett["rules"]}
for p, v in (("T3", T3HEX), ("T4", T4HEX)):
    if p not in have:
        sett["rules"].append({"t": "set", "p": p, "pt": "global", "to": v, "tot": "str"})
print("settings globals now:", [r["p"] for r in sett["rules"]])

_c = [0]
def nid():
    while True:
        _c[0] += 1; cand = ("c9%014x" % _c[0])[:16]
        if cand not in ids:
            ids.add(cand); return cand
new = []; add = lambda n: (new.append(n), n["id"])[1]

GHEAD = ("var APP=global.get('APP'),NCAP=global.get('NCAP'),T0=global.get('T0'),"
         "T3=global.get('T3'),T4=global.get('T4');\nmsg.topic=global.get('reqTopicC');\n")

def cbuilder(name, tmpl):
    return add(dict(id=nid(), type="function", z=ZC, name=name,
                    func=GHEAD + "msg.payload=`" + tmpl + "`;\nreturn msg;",
                    outputs=1, timeout=0, noerr=0, initialize="", finalize="", libs=[],
                    x=700, y=2200, wires=[[OUTC]]))

def group(name, order):
    return add(dict(id=nid(), type="ui_group", name=name, tab=RR, order=order,
                    disp=True, width="6", collapse=True, className=""))

def button(group_id, order, label, builder_id):
    return add(dict(id=nid(), type="ui_button", z=ZC, group=group_id, order=order,
                    width="6", height="1", passthru=False, label=label, tooltip="",
                    color="", bgcolor="", className="", icon="", payload="", payloadType="str",
                    topic="", topicType="str", x=400, y=2200, wires=[[builder_id]]))

# service table: (group, label, csv-template)  -- C-OP, fields after header
SVCS = [
 ("TEDS svc (3,x)", [
   ("TEDS Query (3,1)",  "3,1,1,0x${APP},0x${NCAP},0x${T0},1,12,0"),
   ("TEDS Write (3,3)",  "3,3,1,0x${APP},0x${NCAP},0x${T0},1,60,0,0,deadbeef"),
   ("TEDS Update (3,4)", "3,4,1,0x${APP},0x${NCAP},0x${T0},1,60,0,0,cafe"),
 ]),
 ("Read+ svc (2,2-4)", [
   ("Block read 1ch (2,2)",    "2,2,1,0x${APP},0x${NCAP},0x${T3},3,4,0,0,0"),
   ("Sample multi-ch (2,3)",   "2,3,1,0x${APP},0x${NCAP},0x${T3},3,1:2:3,5,0"),
   ("Block multi-ch (2,4)",    "2,4,1,0x${APP},0x${NCAP},0x${T3},2,1:3,3,0,0,0"),
 ]),
 ("Write+ svc (2,8-12)", [
   ("Block write (2,8)",        "2,8,1,0x${APP},0x${NCAP},0x${T3},4,5,70;72;75,0"),
   ("Sample multi-ch wr (2,9)", "2,9,1,0x${APP},0x${NCAP},0x${T3},1,4,5,66,0"),
   ("Block multi-ch wr (2,10)", "2,10,1,0x${APP},0x${NCAP},0x${T3},1,4,5,55;58,0"),
   ("Multi-TIM write (2,11)",   "2,11,1,0x${APP},0x${NCAP},2,0x${T3}:0x${T4},1:1,4:4,5,44:33,0"),
   ("Block multi-TIM wr (2,12)","2,12,1,0x${APP},0x${NCAP},2,0x${T3}:0x${T4},1:1,4:4,5,22;25:11;15,0"),
 ]),
 ("Async svc (2,13-19)", [
   ("Async block (2,13)",       "2,13,1,0x${APP},0x${NCAP},0x${T3},3,4,0,0,0"),
   ("Async stream (2,15)",      "2,15,1,0x${APP},0x${NCAP},0x${T3},3,5,0,0"),
   ("Async block multi-ch (2,17)", "2,17,1,0x${APP},0x${NCAP},0x${T3},3,1:2:3,3,0,0,0"),
   ("Async block multi-TIM (2,19)","2,19,1,0x${APP},0x${NCAP},2,0x${T3}:0x${T4},2:2,1:3:1:3,2,0,0,0"),
 ]),
 ("Event-multi svc (4,4-9)", [
   ("Sub multi-ch (4,4)",   "4,4,1,0x${APP},0x${NCAP},0x${T3},3,1:2:3,,app0,1,0"),
   ("Unsub multi-ch (4,6)", "4,6,1,0x${APP},0x${NCAP},0x${T3},3,1:2:3,0"),
   ("Sub multi-TIM (4,7)",  "4,7,1,0x${APP},0x${NCAP},2,0x${T3}:0x${T4},2:2,1:3:1:3,,app0,1,0"),
   ("Unsub multi-TIM (4,9)","4,9,1,0x${APP},0x${NCAP},2,0x${T3}:0x${T4},2:2,1:3:1:3,0"),
 ]),
]
order = 40
for gname, items in SVCS:
    gid = group(gname, order); order += 1
    for i, (label, tmpl) in enumerate(items, 1):
        b = cbuilder(label, tmpl)
        button(gid, i, label, b)

# ---- Time-sync group: RR-Sync request + monitors ----
gts = group("Time-sync svc (9)", order); order += 1
rr_b = add(dict(id=nid(), type="function", z=ZC, name="RR-Sync request",
                func="msg.topic='_1451.1.6/RRS/REQ';\nmsg.payload='PTTEST/ncap0,'+(Date.now()/1000).toFixed(6);\nreturn msg;",
                outputs=1, timeout=0, noerr=0, initialize="", finalize="", libs=[], x=700, y=2400,
                wires=[[OUTC]]))
button(gts, 1, "RR-Sync request (9.3)", rr_b)

# ---- Monitors (ui_text) -------------------------------------------- #
gmon = group("Service replies (monitor)", order); order += 1
txt_reply = add(dict(id=nid(), type="ui_text", z=ZC, group=gmon, order=1, width="6", height="3",
                     name="", label="Last C-OP reply", format="{{msg.payload}}",
                     layout="col-center", className="", x=1050, y=2200, wires=[]))
txt_rr = add(dict(id=nid(), type="ui_text", z=ZC, group=gmon, order=2, width="6", height="1",
                  name="", label="RR-Sync offset/delay", format="{{msg.payload}}",
                  layout="row-spread", className="", x=1050, y=2260, wires=[]))
txt_br = add(dict(id=nid(), type="ui_text", z=ZC, group=gmon, order=3, width="6", height="1",
                  name="", label="BR-Sync epoch", format="{{msg.payload}}",
                  layout="row-spread", className="", x=1050, y=2320, wires=[]))

fn_reply = add(dict(id=nid(), type="function", z=ZC, name="raw C reply",
    func=("var s=(''+msg.payload).trim();var f=s.split(',');\n"
          "if(f.length>=3 && (f[2]==='2'||f[2]==='4')){msg.payload=s;return msg;}\nreturn null;"),
    outputs=1, timeout=0, noerr=0, initialize="", finalize="", libs=[], x=820, y=2200, wires=[[txt_reply]]))
add(dict(id=nid(), type="mqtt in", z=ZC, name="C reply mon", topic="_1451.1.6/C/PTTEST/ncap0",
         qos="0", datatype="auto", broker=BROKER, nl=False, rap=False, rh=0, inputs=0,
         x=600, y=2200, wires=[[fn_reply]]))

fn_rr = add(dict(id=nid(), type="function", z=ZC, name="RR-Sync calc",
    func=("var t4=Date.now()/1000;var f=(''+msg.payload).split(',');\n"
          "var t1=parseFloat(f[0]),t2=parseFloat(f[1]),t3=parseFloat(f[2]),srv=f[3]||'?';\n"
          "var off=((t2-t1)+(t3-t4))/2, dly=(t4-t1)-(t3-t2);\n"
          "msg.payload='server='+srv+'  offset='+off.toFixed(6)+'s  delay='+dly.toFixed(6)+'s';return msg;"),
    outputs=1, timeout=0, noerr=0, initialize="", finalize="", libs=[], x=820, y=2260, wires=[[txt_rr]]))
add(dict(id=nid(), type="mqtt in", z=ZC, name="RR-Sync RES", topic="_1451.1.6/RRS/PTTEST/ncap0/RES",
         qos="0", datatype="auto", broker=BROKER, nl=False, rap=False, rh=0, inputs=0,
         x=600, y=2260, wires=[[fn_rr]]))
add(dict(id=nid(), type="mqtt in", z=ZC, name="BR-Sync SYN", topic="_1451.1.6/BRSU/SYN",
         qos="0", datatype="auto", broker=BROKER, nl=False, rap=False, rh=0, inputs=0,
         x=600, y=2320, wires=[[txt_br]]))

j.extend(new)
print("added %d nodes" % len(new))
# validate
allids = {n["id"] for n in j}
for n in j:
    for w in n.get("wires", []) or []:
        for t in (w or []):
            assert t in allids, "dangling %s->%s" % (n["id"], t)
assert len({n["id"] for n in j}) == len(j)
# node --check the generated function bodies
import subprocess, tempfile, os
bad = 0
for n in new:
    if n.get("type") == "function":
        p = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False)
        p.write("var global={get:function(){return '';}};function f(msg){\n%s\n}" % n["func"]); p.close()
        r = subprocess.run(["node", "--check", p.name], capture_output=True, text=True); os.unlink(p.name)
        if r.returncode:
            print("SYNTAX ERR", n["name"], r.stderr.splitlines()[-1]); bad += 1
print("function syntax errors:", bad)
assert bad == 0

json.dump(j, open(SRC, "w"), ensure_ascii=False, indent=4)
try:
    body = json.dumps({"flows": j, "deploymentType": "full"}).encode()
    dreq = urllib.request.Request("http://127.0.0.1:1880/flows", data=body, method="POST",
        headers={"Content-Type": "application/json", "Node-RED-API-Version": "v2"})
    print("DEPLOY:", urllib.request.urlopen(dreq, timeout=30).status)
except Exception as e:
    open(SRC, "w").write(repo_text); print("DEPLOY FAILED, reverted:", repr(e)); sys.exit(1)
print("repo==live:", {n['id'] for n in json.load(urllib.request.urlopen(req, timeout=10))['flows']} == {n['id'] for n in j}, "| nodes", len(j))
