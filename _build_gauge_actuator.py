#!/usr/bin/env python3
# Add a working "Gauge" actuator (1451.1.6 sync_write -> M5) with a reacting gauge to the
# M5 Req/Res groups, rename legacy SERVO -> Gauge and retarget them to the M5 #1 gauge.
import json, urllib.request, sys, subprocess, tempfile, os

SRC="NodeRED.json"
APP ="0x2400250026002700280029003a030f0f"
NCAP="0x2400250026002700280029003a010f0f"
TIM3="0x2400250026002700280029003a020f03"
TIM4="0x2400250026002700280029003a020f04"
ZFLOW="9ac7a3893df687ef"

req=urllib.request.Request("http://127.0.0.1:1880/flows",headers={"Node-RED-API-Version":"v2"})
live=json.load(urllib.request.urlopen(req,timeout=10))["flows"]
repo_text=open(SRC).read(); j=json.loads(repo_text)
if {n["id"] for n in live}!={n["id"] for n in j}:
    print("ABORT: repo/live diverge"); sys.exit(1)
print("sync OK (%d nodes)"%len(j))
byid={n["id"]:n for n in j}; ids=set(byid)
def byname(nm): return next((n for n in j if n.get("name")==nm),None)
def bylabel(lb): return next((n for n in j if n.get("label")==lb),None)
def grpid(nm): return next((n["id"] for n in j if n.get("type")=="ui_group" and n["name"]==nm),None)

M5OUT=byname("M5 C-OP req")["id"]                 # mqtt out -> C/PTTEST/ncap0
GA=grpid("M5 #1  Req/Res (TIM3)"); GB=grpid("M5 #2  Req/Res (TIM4)")

_c=[0]
def nid():
    while True:
        _c[0]+=1; cand=("c7%014x"%_c[0])[:16]
        if cand not in ids: ids.add(cand); return cand
new=[]; add=lambda n:(new.append(n),n["id"])[1]

WRITER="""// Gauge actuator -> 1451.1.6 sync_write (2,7,1) to NCAP -> M5 gauge channel (ch4)
var v = parseInt(msg.payload, 10);
if (isNaN(v)) return null;
v = Math.max(0, Math.min(100, v));
msg.payload = '2,7,1,__APP__,__NCAP__,__TIM__,4,0,' + v;
return msg;"""

def make_gauge_actuator(group, tim, order_slider, order_gauge, y):
    g=dict(id=nid(),type="ui_gauge",z=ZFLOW,group=group,order=order_gauge,width="6",height="3",
           gtype="gage",title="Gauge (actual)",label="0-100",format="{{value}}",min="0",max="100",
           colors=["#0094ce","#0094ce","#0094ce"],seg1="",seg2="",diff=False,className="",x=1050,y=y,wires=[])
    add(g)
    b=dict(id=nid(),type="function",z=ZFLOW,name="Gauge write "+tim[-2:],
           func=WRITER.replace("__APP__",APP).replace("__NCAP__",NCAP).replace("__TIM__",tim),
           outputs=1,timeout=0,noerr=0,initialize="",finalize="",libs=[],x=820,y=y+40,wires=[[M5OUT]])
    add(b)
    s=dict(id=nid(),type="ui_slider",z=ZFLOW,group=group,order=order_slider,width="6",height="1",
           name="",label="Gauge write (0-100)",tooltip="",passthru=True,outs="end",
           topic="payload",topicType="msg",min=0,max="100",step=1,className="",x=560,y=y+40,
           wires=[[g["id"],b["id"]]])    # slider -> reacting gauge + write-builder
    add(s)

# M5 Req/Res groups: gauge actuator (slider order 7, gauge order 8 -> after read widgets)
make_gauge_actuator(GA, TIM3, 7, 8, 1500)
make_gauge_actuator(GB, TIM4, 7, 8, 1700)
print("added M5 Gauge actuators (slider + reacting gauge + sync_write) to both groups")

# ---- legacy SERVO -> Gauge: rename + retarget to M5 #1 gauge + reacting gauge ----
# D0C (CSV) SyncWriteCmd: TIM2 ch1 -> TIM3 ch4
sw=byname("SyncWriteCmd")
sw["func"]=sw["func"].replace("020f02,1,0,","020f03,4,0,")
assert "020f03,4,0," in sw["func"], "D0C retarget failed"
# D0 (binary) SERVO D0 BIN (the one feeding D0 SERVO slider, TIM2): buf[52]=2->3, buf[54]=1->4
d0bin=byid["92cdf83959118817"]
d0bin["func"]=d0bin["func"].replace("buf[52] = 2; // 15","buf[52] = 3; // 15").replace("buf[54] = 1; //","buf[54] = 4; //")
assert "buf[52] = 3; // 15" in d0bin["func"] and "buf[54] = 4; //" in d0bin["func"], "D0 retarget failed"

# rename groups + slider labels
for gname,new_g in (("D0C SERVO","D0C Gauge"),("D0 SERVO","D0 Gauge")):
    g=next(n for n in j if n.get("type")=="ui_group" and n["name"]==gname); g["name"]=new_g
for sl in ("D0C SERVO","D0 SERVO"):
    s=bylabel(sl); s["label"]="Gauge"
print("legacy SERVO renamed -> Gauge and retargeted to M5 #1 gauge (TIM3 ch4)")

# add reacting gauge to each legacy group + wire from its slider
def add_react_gauge(slider_id, group):
    g=dict(id=nid(),type="ui_gauge",z=byid[slider_id]["z"],group=group,order=9,width="6",height="3",
           gtype="gage",title="Gauge (actual)",label="0-100",format="{{value}}",min="0",max="100",
           colors=["#0094ce","#0094ce","#0094ce"],seg1="",seg2="",diff=False,className="",x=300,y=2000,wires=[])
    add(g)
    s=byid[slider_id]; s["wires"][0]=list(s["wires"][0])+[g["id"]]   # slider -> (builder) + reacting gauge
add_react_gauge("975791de93d333ec", grpid("D0C Gauge"))
add_react_gauge("f09eb6e12bda2bff", grpid("D0 Gauge"))

j.extend(new)
print("added %d new nodes"%len(new))

# validate
allids={n["id"] for n in j}
for n in j:
    for w in n.get("wires",[]) or []:
        for t in (w or []): assert t in allids, "dangling %s->%s"%(n["id"],t)
assert len({n["id"] for n in j})==len(j)
for fn in [WRITER.replace("__APP__",APP).replace("__NCAP__",NCAP).replace("__TIM__",TIM3), sw["func"], d0bin["func"]]:
    p=tempfile.NamedTemporaryFile("w",suffix=".js",delete=False);p.write("function f(msg){\n%s\n}\n"%fn);p.close()
    r=subprocess.run(["node","--check",p.name],capture_output=True,text=True);os.unlink(p.name)
    assert r.returncode==0,"syntax: "+r.stderr
print("validation OK")

json.dump(j,open(SRC,"w"),ensure_ascii=False,indent=4)
try:
    body=json.dumps({"flows":j,"deploymentType":"full"}).encode()
    dreq=urllib.request.Request("http://127.0.0.1:1880/flows",data=body,method="POST",
        headers={"Content-Type":"application/json","Node-RED-API-Version":"v2"})
    print("DEPLOY:",urllib.request.urlopen(dreq,timeout=30).status)
except Exception as e:
    open(SRC,"w").write(repo_text); print("DEPLOY FAILED, reverted:",repr(e)); sys.exit(1)
live2=json.load(urllib.request.urlopen(req,timeout=10))["flows"]
print("repo==live:",{n['id'] for n in live2}=={n['id'] for n in j},"| nodes",len(j))
