#!/usr/bin/env python3
# Add D0 (binary) buttons for every new service, symmetric to the D0C (CSV) ones.
# Base on the LIVE flow (the full version the user chose); write repo = result.
import json, urllib.request, sys, subprocess, tempfile, os

RR = "86483328.1e604"            # ui_tab Request-Response
ZD = "a4c183a95164db51"          # D0 flow canvas
OUTD = "00a1510000000004"        # existing empty-topic D0 publisher (-> msg.topic)
D0SET = "00a1510000000002"       # D0 settings change node
T3HEX = "2400250026002700280029003a020f03"
T4HEX = "2400250026002700280029003a020f04"

req = urllib.request.Request("http://127.0.0.1:1880/flows", headers={"Node-RED-API-Version": "v2"})
j = json.load(urllib.request.urlopen(req, timeout=10))["flows"]
print("base = LIVE flow (%d nodes)" % len(j))
ids = {n["id"] for n in j}; byid = {n["id"]: n for n in j}
for k in ("00a1510000000004", "86483328.1e604", "a4c183a95164db51"):
    assert k in ids, "missing infra " + k

# add T3/T4 to D0 settings node (global ctx is shared, but keep it self-contained)
sett = byid.get(D0SET)
if sett:
    have = {r.get("p") for r in sett["rules"]}
    for p, v in (("T3", T3HEX), ("T4", T4HEX)):
        if p not in have:
            sett["rules"].append({"t": "set", "p": p, "pt": "global", "to": v, "tot": "str"})

_c = [0]
def nid():
    while True:
        _c[0] += 1; cand = ("ca%014x" % _c[0])[:16]
        if cand not in ids:
            ids.add(cand); return cand
new = []; add = lambda n: (new.append(n), n["id"])[1]

ENCODER = r"""var a=[];
function p(){for(var i=0;i<arguments.length;i++)a.push(arguments[i]&255);}
function u16(n){p(n>>8,n);}
function u32(n){p(n>>>24,n>>16,n>>8,n);}
function uuid(s){s=(''+s).replace(/^0x/,'');for(var i=0;i<32;i+=2)a.push(parseInt(s.substr(i,2),16));}
function str(s){s=''+s;for(var i=0;i<s.length;i++)a.push(s.charCodeAt(i)&255);a.push(0);}
var lp=-1;
(msg.spec||[]).forEach(function(f){var t=f[0],v=f[1];
 if(t==='u8')p(v);else if(t==='u16')u16(v);else if(t==='u32')u32(v);
 else if(t==='uuid')uuid(v);else if(t==='str')str(v);
 else if(t==='time8'){for(var i=0;i<8;i++)a.push(0);}
 else if(t==='oct'){for(var i=0;i<v.length;i+=2)a.push(parseInt(v.substr(i,2),16));}
 else if(t==='u16a')v.forEach(u16);
 else if(t==='uuida')v.forEach(uuid);
 else if(t==='stra')v.forEach(str);
 else if(t==='len'){lp=a.length;a.push(0,0);}});
if(lp>=0){var L=a.length-lp-2;a[lp]=(L>>8)&255;a[lp+1]=L&255;}
msg.topic=global.get('reqTopicD0');
msg.payload=Buffer.from(a);
return msg;"""
encoder = add(dict(id=nid(), type="function", z=ZD, name="D0 binary encode", func=ENCODER,
                   outputs=1, timeout=0, noerr=0, initialize="", finalize="", libs=[],
                   x=900, y=2200, wires=[[OUTD]]))

def fjs(f):
    t = f[0]
    if t in ("u8", "u16", "u32"): return "['%s',%d]" % (t, f[1])
    if t == "uuid": return "['uuid',%s]" % f[1]
    if t == "uuida": return "['uuida',[%s]]" % ",".join(f[1])
    if t == "u16a": return "['u16a',[%s]]" % ",".join(str(x) for x in f[1])
    if t == "str": return "['str',%s]" % json.dumps(f[1])
    if t == "stra": return "['stra',[%s]]" % ",".join(json.dumps(x) for x in f[1])
    if t == "oct": return "['oct',%s]" % json.dumps(f[1])
    if t in ("time8", "len"): return "['%s']" % t
    raise ValueError(t)

HEAD = "var APP=global.get('APP'),NCAP=global.get('NCAP'),T0=global.get('T0'),T3=global.get('T3'),T4=global.get('T4');\n"
def specfn(name, fields):
    body = HEAD + "msg.spec=[" + ",".join(fjs(f) for f in fields) + "];\nreturn msg;"
    return add(dict(id=nid(), type="function", z=ZD, name=name, func=body, outputs=1, timeout=0,
                    noerr=0, initialize="", finalize="", libs=[], x=680, y=2200, wires=[[encoder]]))

def group(name, order):
    return add(dict(id=nid(), type="ui_group", name=name, tab=RR, order=order,
                    disp=True, width="6", collapse=True, className=""))
def button(gid, order, label, fnid):
    return add(dict(id=nid(), type="ui_button", z=ZD, group=gid, order=order, width="6", height="1",
                    passthru=False, label=label, tooltip="", color="", bgcolor="", className="",
                    icon="", payload="", payloadType="str", topic="", topicType="str",
                    x=420, y=2200, wires=[[fnid]]))

H = lambda nt, ni: [("u8", nt), ("u8", ni), ("u8", 1), ("len",)]
APP = "APP"; NCAP = "NCAP"; T0 = "T0"; T3 = "T3"; T4 = "T4"
SVCS = [
 ("TEDS svc D0 (3,x)", [
  ("TEDS Query (3,1)",  H(3,1)+[("uuid",APP),("uuid",NCAP),("uuid",T0),("u16",1),("u8",12),("time8",)]),
  ("TEDS Write (3,3)",  H(3,3)+[("uuid",APP),("uuid",NCAP),("uuid",T0),("u16",1),("u8",60),("u32",0),("time8",),("oct","deadbeef")]),
  ("TEDS Update (3,4)", H(3,4)+[("uuid",APP),("uuid",NCAP),("uuid",T0),("u16",1),("u8",60),("u32",0),("time8",),("oct","cafe")]),
 ]),
 ("Read+ svc D0 (2,2-4)", [
  ("Block read 1ch (2,2)",  H(2,2)+[("uuid",APP),("uuid",NCAP),("uuid",T3),("u16",3),("u32",4),("time8",),("time8",),("time8",)]),
  ("Sample multi-ch (2,3)", H(2,3)+[("uuid",APP),("uuid",NCAP),("uuid",T3),("u16",3),("u16a",[1,2,3]),("u8",5),("time8",)]),
  ("Block multi-ch (2,4)",  H(2,4)+[("uuid",APP),("uuid",NCAP),("uuid",T3),("u16",2),("u16a",[1,3]),("u32",3),("time8",),("time8",),("time8",)]),
 ]),
 ("Write+ svc D0 (2,8-12)", [
  ("Block write (2,8)",        H(2,8)+[("uuid",APP),("uuid",NCAP),("uuid",T3),("u16",4),("u8",5),("str","70;72;75"),("time8",)]),
  ("Sample multi-ch wr (2,9)", H(2,9)+[("uuid",APP),("uuid",NCAP),("uuid",T3),("u16",1),("u16a",[4]),("u8",5),("stra",["66"]),("time8",)]),
  ("Block multi-ch wr (2,10)", H(2,10)+[("uuid",APP),("uuid",NCAP),("uuid",T3),("u16",1),("u16a",[4]),("u8",5),("stra",["55;58"]),("time8",)]),
  ("Multi-TIM write (2,11)",   H(2,11)+[("uuid",APP),("uuid",NCAP),("u16",2),("uuida",[T3,T4]),("u16a",[1,1]),("u16a",[4,4]),("u8",5),("stra",["44","33"]),("time8",)]),
  ("Block multi-TIM wr (2,12)",H(2,12)+[("uuid",APP),("uuid",NCAP),("u16",2),("uuida",[T3,T4]),("u16a",[1,1]),("u16a",[4,4]),("u8",5),("stra",["22;25","11;15"]),("time8",)]),
 ]),
 ("Async svc D0 (2,13-19)", [
  ("Async block (2,13)",          H(2,13)+[("uuid",APP),("uuid",NCAP),("uuid",T3),("u16",3),("u32",4),("time8",),("time8",),("time8",)]),
  ("Async stream (2,15)",         H(2,15)+[("uuid",APP),("uuid",NCAP),("uuid",T3),("u16",3),("u8",5),("time8",),("time8",)]),
  ("Async block multi-ch (2,17)", H(2,17)+[("uuid",APP),("uuid",NCAP),("uuid",T3),("u16",3),("u16a",[1,2,3]),("u32",3),("time8",),("time8",),("time8",)]),
  ("Async block multi-TIM (2,19)",H(2,19)+[("uuid",APP),("uuid",NCAP),("u16",2),("uuida",[T3,T4]),("u16a",[2,2]),("u16a",[1,3,1,3]),("u32",2),("time8",),("time8",),("time8",)]),
 ]),
 ("Event-multi svc D0 (4,4-9)", [
  ("Sub multi-ch (4,4)",   H(4,4)+[("uuid",APP),("uuid",NCAP),("uuid",T3),("u16",3),("u16a",[1,2,3]),("str",""),("str","app0"),("time8",),("time8",)]),
  ("Unsub multi-ch (4,6)", H(4,6)+[("uuid",APP),("uuid",NCAP),("uuid",T3),("u16",3),("u16a",[1,2,3]),("u16",0)]),
  ("Sub multi-TIM (4,7)",  H(4,7)+[("uuid",APP),("uuid",NCAP),("u16",2),("uuida",[T3,T4]),("u16a",[2,2]),("u16a",[1,3,1,3]),("str",""),("str","app0"),("time8",),("time8",)]),
  ("Unsub multi-TIM (4,9)",H(4,9)+[("uuid",APP),("uuid",NCAP),("u16",2),("uuida",[T3,T4]),("u16a",[2,2]),("u16a",[1,3,1,3]),("u16",0)]),
 ]),
]
order = 60
for gname, items in SVCS:
    gid = group(gname, order); order += 1
    for i, (label, fields) in enumerate(items, 1):
        fn = specfn(label + " [D0]", fields)
        button(gid, i, label, fn)

j.extend(new)
print("added %d D0 nodes" % len(new))
# validate
allids = {n["id"] for n in j}
for n in j:
    for w in n.get("wires", []) or []:
        for t in (w or []):
            assert t in allids, "dangling"
assert len({n["id"] for n in j}) == len(j)
bad = 0
for n in new:
    if n.get("type") == "function":
        p = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False)
        p.write("var global={get:function(){return '';}},Buffer={from:function(x){return x;}};function f(msg){\n%s\n}" % n["func"]); p.close()
        r = subprocess.run(["node", "--check", p.name], capture_output=True, text=True); os.unlink(p.name)
        if r.returncode:
            print("SYNTAX", n["name"], r.stderr.splitlines()[-1]); bad += 1
print("syntax errors:", bad); assert bad == 0

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
