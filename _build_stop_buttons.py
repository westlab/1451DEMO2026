#!/usr/bin/env python3
# Add "Unsubscribe (Stop)" buttons to the D0/D0C EXT groups: event(4,3,1) + heartbeat(4,12,1).
import json, urllib.request, sys, subprocess, tempfile, os

SRC="NodeRED.json"
ZC="0c7a731dc303bfda"; OUTC="00a1510000000019"; GC="00a1510000000016"   # D0C tab/out/EXT-group
ZD="a4c183a95164db51"; OUTD="00a1510000000004"; GD="00a1510000000001"   # D0  tab/out/EXT-group

req=urllib.request.Request("http://127.0.0.1:1880/flows",headers={"Node-RED-API-Version":"v2"})
live=json.load(urllib.request.urlopen(req,timeout=10))["flows"]
repo_text=open(SRC).read(); j=json.loads(repo_text)
if {n["id"] for n in live}!={n["id"] for n in j}: print("ABORT diverge"); sys.exit(1)
print("sync OK (%d nodes)"%len(j)); ids={n["id"] for n in j}
_c=[0]
def nid():
    while True:
        _c[0]+=1; cand=("c8%014x"%_c[0])[:16]
        if cand not in ids: ids.add(cand); return cand
new=[]; add=lambda n:(new.append(n),n["id"])[1]

# CSV builders (D0C): mirror "D0C Event subscribe"
CSV_EVENT="""var APP=global.get('APP'),NCAP=global.get('NCAP'),T0=global.get('T0');
msg.topic=global.get('reqTopicC');
msg.payload='4,3,1,0x'+APP+',0x'+NCAP+',0x'+T0+',1,0';   // event unsubscribe (4,3,1)
return msg;"""
CSV_HB="""var APP=global.get('APP');
msg.topic=global.get('reqTopicC');
msg.payload='4,12,1,0x'+APP;                              // heartbeat unsubscribe (4,12,1)
return msg;"""
# Binary builders (D0): mirror "D0 Event subscribe"
BIN_PRE="""var APP=global.get('APP'),NCAP=global.get('NCAP'),T0=global.get('T0');
function hx(s){s=String(s).replace(/^0x/,'');var b=[];for(var k=0;k<s.length;k+=2){b.push(parseInt(s.substr(k,2),16));}return b;}
function u16(n){return [(n>>8)&255,n&255];}
"""
BIN_EVENT=BIN_PRE+"""var a=[4,3,1,0,0].concat(hx(APP)).concat(hx(NCAP)).concat(hx(T0)).concat(u16(1)).concat(u16(0));
var L=a.length-6;a[3]=(L>>8)&255;a[4]=L&255;
msg.topic=global.get('reqTopicD0');msg.payload=Buffer.from(a);return msg;"""
BIN_HB=BIN_PRE+"""var a=[4,12,1,0,0].concat(hx(APP));
var L=a.length-6;a[3]=(L>>8)&255;a[4]=L&255;
msg.topic=global.get('reqTopicD0');msg.payload=Buffer.from(a);return msg;"""

def builder(z,name,code,out,y):
    return add(dict(id=nid(),type="function",z=z,name=name,func=code,outputs=1,timeout=0,
                    noerr=0,initialize="",finalize="",libs=[],x=700,y=y,wires=[[out]]))
def button(z,group,label,bid,order,y):
    return add(dict(id=nid(),type="ui_button",z=z,group=group,order=order,width="3",height="1",
                    passthru=False,label=label,tooltip="",color="#ffffff",bgcolor="#c0392b",
                    className="",icon="",payload="",payloadType="str",topic="",topicType="str",
                    x=420,y=y,wires=[[bid]]))

# D0C (CSV)
b=builder(ZC,"D0C Event unsubscribe",CSV_EVENT,OUTC,1400); button(ZC,GC,"Event Unsub (Stop)",b,8,1400)
b=builder(ZC,"D0C Heartbeat unsubscribe",CSV_HB,OUTC,1460); button(ZC,GC,"Heartbeat Stop",b,9,1460)
# D0 (binary)
b=builder(ZD,"D0 Event unsubscribe",BIN_EVENT,OUTD,1400); button(ZD,GD,"Event Unsub (Stop)",b,8,1400)
b=builder(ZD,"D0 Heartbeat unsubscribe",BIN_HB,OUTD,1460); button(ZD,GD,"Heartbeat Stop",b,9,1460)

j.extend(new); print("added %d nodes (4 Stop buttons + 4 builders)"%len(new))
# validate
allids={n["id"] for n in j}
for n in j:
    for w in n.get("wires",[]) or []:
        for t in (w or []): assert t in allids,"dangling"
for code in (CSV_EVENT,CSV_HB,BIN_EVENT,BIN_HB):
    p=tempfile.NamedTemporaryFile("w",suffix=".js",delete=False);p.write("function f(msg){var global={get:function(){return '';}};\n%s\n}"%code);p.close()
    r=subprocess.run(["node","--check",p.name],capture_output=True,text=True);os.unlink(p.name)
    assert r.returncode==0,"syntax: "+r.stderr
print("validation OK")
json.dump(j,open(SRC,"w"),ensure_ascii=False,indent=4)
try:
    body=json.dumps({"flows":j,"deploymentType":"full"}).encode()
    dreq=urllib.request.Request("http://127.0.0.1:1880/flows",data=body,method="POST",headers={"Content-Type":"application/json","Node-RED-API-Version":"v2"})
    print("DEPLOY:",urllib.request.urlopen(dreq,timeout=30).status)
except Exception as e:
    open(SRC,"w").write(repo_text); print("DEPLOY FAILED, reverted:",repr(e)); sys.exit(1)
print("repo==live:",{n['id'] for n in json.load(urllib.request.urlopen(req,timeout=10))['flows']}=={n['id'] for n in j},"| nodes",len(j))
