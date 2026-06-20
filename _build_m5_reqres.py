#!/usr/bin/env python3
# Add "M5 Req/Res" groups to the Request-Response tab: per-unit Temp/Humid/CO2/Gauge
# read buttons (1451.1.6 C-OP CSV read) + decoded text + CO2 gauge. Verified NCAP responds.
import json, urllib.request, sys

SRC="NodeRED.json"
RRTAB="86483328.1e604"          # ui_tab Request-Response
ZFLOW="9ac7a3893df687ef"        # put flow nodes on the M5 flow canvas
BROKER="39d5aa93d6951ccb"
CTOPIC="_1451.1.6/C/PTTEST/ncap0"
APP ="0x2400250026002700280029003a030f0f"
NCAP="0x2400250026002700280029003a010f0f"
TIM3="0x2400250026002700280029003a020f03"
TIM4="0x2400250026002700280029003a020f04"

# pull-before-deploy sync check
req=urllib.request.Request("http://127.0.0.1:1880/flows",headers={"Node-RED-API-Version":"v2"})
live=json.load(urllib.request.urlopen(req,timeout=10))["flows"]
repo_text=open(SRC).read(); j=json.loads(repo_text)
if {n["id"] for n in live}!={n["id"] for n in j}:
    print("ABORT: repo/live diverge"); sys.exit(1)
print("sync OK (%d nodes)"%len(j))
ids={n["id"] for n in j}

_c=[0]
def nid():
    while True:
        _c[0]+=1; cand=("c6%014x"%_c[0])[:16]
        if cand not in ids: ids.add(cand); return cand

new=[]
def add(n): new.append(n); return n["id"]

# shared request publisher
mout=dict(id=nid(),type="mqtt out",z=ZFLOW,name="M5 C-OP req",topic=CTOPIC,qos="0",retain="",
          respTopic="",contentType="",userProps="",correl="",expiry="",broker=BROKER,x=700,y=1100,wires=[])
add(mout)

UNITS=[dict(idx=0,tim=TIM3,name="M5 #1  Req/Res (TIM3)",order=5),
       dict(idx=1,tim=TIM4,name="M5 #2  Req/Res (TIM4)",order=6)]
GROUPS={}; disp={}   # idx -> (text_id, gauge_id)

for u in UNITS:
    gid=nid()
    add(dict(id=gid,type="ui_group",name=u["name"],tab=RRTAB,order=u["order"],
             disp=True,width="6",collapse=False,className=""))
    GROUPS[u["idx"]]=gid
    by=1200+u["idx"]*360
    txt=dict(id=nid(),type="ui_text",z=ZFLOW,group=gid,order=6,width="6",height="2",name="",
             label="Last response",format="{{msg.payload}}",layout="col-center",className="",x=1050,y=by+60,wires=[])
    gau=dict(id=nid(),type="ui_gauge",z=ZFLOW,group=gid,order=5,width="3",height="3",gtype="gage",
             title="CO2 ppm (req/res)",label="ppm",format="{{value}}",min="400",max="2000",
             colors=["#00b500","#e6e600","#ca3838"],seg1="1000",seg2="1500",diff=False,className="",x=1050,y=by,wires=[])
    add(txt); add(gau); disp[u["idx"]]=(txt["id"],gau["id"])
    chans=[("Temp read",1),("Humid read",2),("CO2 read",3),("Gauge read",4)]
    for i,(lbl,ch) in enumerate(chans):
        payload="2,1,1,%s,%s,%s,%d,0,0"%(APP,NCAP,u["tim"],ch)
        add(dict(id=nid(),type="ui_button",z=ZFLOW,group=gid,order=i+1,width="3",height="1",
                 passthru=False,label=lbl,tooltip="",color="",bgcolor="",className="",icon="",
                 payload=payload,payloadType="str",topic="",topicType="str",
                 x=400,y=by+i*40,wires=[[mout["id"]]]))

# reply decoder -> [u1 text, u1 co2 gauge, u2 text, u2 co2 gauge]
DEC="""var s = ('' + msg.payload).trim();
var f = s.split(',');
// read reply: 2,1,2,0,app,ncap,tim,ch,val,ts
if (f.length < 9 || f[0] !== '2' || f[1] !== '1' || f[2] !== '2') return [null,null,null,null];
var tim = (f[6] || '').toLowerCase();
var ch  = f[7];
var val = parseFloat(f[8]);
var names = {'1':'Temp','2':'Humid','3':'CO2','4':'Gauge'};
var units = {'1':'K','2':'%','3':'ppm','4':''};
var extra = (ch === '1') ? ' (' + (val - 273.15).toFixed(1) + ' \\u00b0C)' : '';
var line = 'ch' + ch + ' ' + (names[ch]||'?') + ' = ' + val + ' ' + (units[ch]||'') + extra;
var T = '__TIM3__', U = '__TIM4__';
var tmsg = {payload: line};
var gmsg = {payload: val};
if (tim === T) return [tmsg, ch === '3' ? gmsg : null, null, null];
if (tim === U) return [null, null, tmsg, ch === '3' ? gmsg : null];
return [null,null,null,null];""".replace("__TIM3__", TIM3).replace("__TIM4__", TIM4)

dec=dict(id=nid(),type="function",z=ZFLOW,name="M5 reply decode",func=DEC,outputs=4,timeout=0,
         noerr=0,initialize="",finalize="",libs=[],x=820,y=1260,
         wires=[[disp[0][0],disp[0][1] and []],[],[],[]])
# fix wires properly
dec["wires"]=[[disp[0][0]],[disp[0][1]],[disp[1][0]],[disp[1][1]]]
add(dec)
min_=dict(id=nid(),type="mqtt in",z=ZFLOW,name="M5 C-OP reply",topic=CTOPIC,qos="0",datatype="auto",
          broker=BROKER,nl=False,rap=False,rh=0,inputs=0,x=600,y=1260,wires=[[dec["id"]]])
add(min_)

j.extend(new)
print("added %d nodes (groups+buttons+decoder)"%len(new))

# validate
allids={n["id"] for n in j}
for n in j:
    for w in n.get("wires",[]) or []:
        for t in (w or []):
            assert t in allids, "dangling %s->%s"%(n["id"],t)
assert len({n["id"] for n in j})==len(j), "dup ids"
# function syntax via node --check
import subprocess,tempfile,os
src="function f(msg){\n%s\n}\n"%DEC
p=tempfile.NamedTemporaryFile("w",suffix=".js",delete=False);p.write(src);p.close()
r=subprocess.run(["node","--check",p.name],capture_output=True,text=True);os.unlink(p.name)
assert r.returncode==0, "decoder syntax: "+r.stderr
print("validation OK: no dangling wires, unique ids, decoder syntax OK")

json.dump(j,open(SRC,"w"),ensure_ascii=False,indent=4)
# deploy
try:
    body=json.dumps({"flows":j,"deploymentType":"full"}).encode()
    dreq=urllib.request.Request("http://127.0.0.1:1880/flows",data=body,method="POST",
        headers={"Content-Type":"application/json","Node-RED-API-Version":"v2"})
    print("DEPLOY:",urllib.request.urlopen(dreq,timeout=30).status)
except Exception as e:
    open(SRC,"w").write(repo_text); print("DEPLOY FAILED, reverted:",repr(e)); sys.exit(1)
live2=json.load(urllib.request.urlopen(req,timeout=10))["flows"]
print("repo==live:",{n['id'] for n in live2}=={n['id'] for n in j})
