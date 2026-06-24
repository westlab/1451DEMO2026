#!/usr/bin/env python3
# Rebuild legacy single-read (2,1) / read-TEDS (3,2) buttons to the NEW method:
#   D0C -> global+template builder (with 0 length col) ; D0 -> spec + shared encoder.
import json, urllib.request, sys, re, subprocess, tempfile, os

COUT="00a1510000000019"; DOUT="00a1510000000004"; ENC="ca00000000000001"
ZC="0c7a731dc303bfda"; ZD="a4c183a95164db51"
LEGACY_GROUPS={"D0C TEMP/HUMID","D0C","D0 TEMP/HUMID","D0","TEMPREQ"}

req=urllib.request.Request("http://127.0.0.1:1880/flows",headers={"Node-RED-API-Version":"v2"})
j=json.load(urllib.request.urlopen(req,timeout=10))["flows"]
print("base LIVE (%d nodes)"%len(j)); ids={n["id"] for n in j}
groups={n["id"]:n.get("name") for n in j if n.get("type")=="ui_group"}
assert ENC in ids, "encoder missing"

_c=[0]
def nid():
    while True:
        _c[0]+=1; cand=("cc%014x"%_c[0])[:16]
        if cand not in ids: ids.add(cand); return cand
new=[]; add=lambda n:(new.append(n),n["id"])[1]

SINGLE={"Temp":("T0",1),"Humid":("T1",1)}
TEDS={"Temp":("T0",1,1),"Humid":("T1",1,1),"Servo":("T2",1,1),"Security":("T0",1,16),
      "Channel":("T0",1,3),"Meta":("T0",1,1),"Name":("T0",1,12),"Phys":("T0",1,13)}

GHEAD=("var APP=global.get('APP'),NCAP=global.get('NCAP'),T0=global.get('T0'),"
       "T1=global.get('T1'),T2=global.get('T2'),T3=global.get('T3'),T4=global.get('T4');\n"
       "msg.topic=global.get('reqTopicC');\n")
def d0c_builder(name,tmpl):
    return add(dict(id=nid(),type="function",z=ZC,name=name,func=GHEAD+"msg.payload=`"+tmpl+"`;\nreturn msg;",
                    outputs=1,timeout=0,noerr=0,initialize="",finalize="",libs=[],x=700,y=2800,wires=[[COUT]]))
SHEAD=("var APP=global.get('APP'),NCAP=global.get('NCAP'),T0=global.get('T0'),"
       "T1=global.get('T1'),T2=global.get('T2');\n")
def fjs(f):
    t=f[0]
    if t in("u8","u16","u32"): return "['%s',%d]"%(t,f[1])
    if t=="uuid": return "['uuid',%s]"%f[1]
    if t in("time8","len"): return "['%s']"%t
    raise ValueError(t)
def d0_builder(name,fields):
    body=SHEAD+"msg.spec=["+",".join(fjs(f) for f in fields)+"];\nreturn msg;"
    return add(dict(id=nid(),type="function",z=ZD,name=name,func=body,outputs=1,timeout=0,
                    noerr=0,initialize="",finalize="",libs=[],x=680,y=2800,wires=[[ENC]]))

rebuilt=0
for n in j:
    if n.get("type")!="ui_button": continue
    if groups.get(n.get("group")) not in LEGACY_GROUPS: continue
    lab=n.get("label","")
    enc="D0C" if lab.startswith("D0C ") else "D0"
    m=re.search(r"\(([^)]+)\)",lab)
    detail=m.group(1) if m else None
    if "single read" in lab and detail in SINGLE:
        tim,ch=SINGLE[detail]
        if enc=="D0C":
            b=d0c_builder("single read %s [new]"%detail, "2,1,1,0,0x${APP},0x${NCAP},0x${%s},%d,5,0"%(tim,ch))
        else:
            b=d0_builder("single read %s [new]"%detail,
                [("u8",2),("u8",1),("u8",1),("len",),("uuid","APP"),("uuid","NCAP"),("uuid",tim),("u16",ch),("u8",5),("time8",)])
    elif "read TEDS" in lab and detail in TEDS:
        tim,ch,code=TEDS[detail]
        if enc=="D0C":
            b=d0c_builder("read TEDS %s [new]"%detail, "3,2,1,0,0x${APP},0x${NCAP},0x${%s},%d,%d,0,0"%(tim,ch,code))
        else:
            b=d0_builder("read TEDS %s [new]"%detail,
                [("u8",3),("u8",2),("u8",1),("len",),("uuid","APP"),("uuid","NCAP"),("uuid",tim),("u16",ch),("u8",code),("u32",0),("time8",)])
    else:
        continue
    n["wires"]=[[b]]            # repoint button to the new builder
    n["payload"]=""            # clear any static payload
    rebuilt+=1
print("rebuilt %d legacy buttons (-> new method)"%rebuilt)

j.extend(new)
# validate
allids={n["id"] for n in j}
for n in j:
    for w in n.get("wires",[]) or []:
        for t in (w or []): assert t in allids,"dangling"
bad=0
for n in new:
    if n.get("type")=="function":
        p=tempfile.NamedTemporaryFile("w",suffix=".js",delete=False)
        p.write("var global={get:function(){return '';}};function f(msg){\n%s\n}"%n["func"]); p.close()
        r=subprocess.run(["node","--check",p.name],capture_output=True,text=True); os.unlink(p.name)
        if r.returncode: print("SYNTAX",n["name"],r.stderr.splitlines()[-1]); bad+=1
print("syntax errors:",bad); assert bad==0

json.dump(j,open("NodeRED.json","w"),ensure_ascii=False,indent=4)
try:
    body=json.dumps({"flows":j,"deploymentType":"full"}).encode()
    dreq=urllib.request.Request("http://127.0.0.1:1880/flows",data=body,method="POST",
        headers={"Content-Type":"application/json","Node-RED-API-Version":"v2"})
    print("DEPLOY:",urllib.request.urlopen(dreq,timeout=30).status)
except Exception as e:
    print("DEPLOY FAILED:",repr(e)); sys.exit(1)
print("repo==live:",{n['id'] for n in json.load(urllib.request.urlopen(req,timeout=10))['flows']}=={n['id'] for n in j},"| nodes",len(j))
