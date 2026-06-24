#!/usr/bin/env python3
# Convert the inline-binary D0 builders (EXT + SERVO gauge) to the spec method so
# EVERY D0 builder goes through the single shared 'D0 binary encode'.
import json, urllib.request, sys, subprocess, tempfile, os

ZD = "a4c183a95164db51"
ENC = "ca00000000000001"     # D0 binary encode
req = urllib.request.Request("http://127.0.0.1:1880/flows", headers={"Node-RED-API-Version": "v2"})
j = json.load(urllib.request.urlopen(req, timeout=10))["flows"]
print("base LIVE (%d nodes)" % len(j))
byid = {n["id"]: n for n in j}; ids = set(byid)
assert ENC in ids

_c = [0]
def nid():
    while True:
        _c[0] += 1; cand = ("cd%014x" % _c[0])[:16]
        if cand not in ids: ids.add(cand); return cand
new = []; add = lambda n: (new.append(n), n["id"])[1]

SHEAD = ("var APP=global.get('APP'),NCAP=global.get('NCAP'),T0=global.get('T0'),"
         "T1=global.get('T1'),T2=global.get('T2'),T3=global.get('T3'),T4=global.get('T4');\n")
def fjs(f):
    t = f[0]
    if t in ("u8", "u16", "u32"): return "['%s',%d]" % (t, f[1])
    if t == "uuid": return "['uuid',%s]" % f[1]
    if t == "uuida": return "['uuida',[%s]]" % ",".join(f[1])
    if t == "u16a": return "['u16a',[%s]]" % ",".join(str(x) for x in f[1])
    if t == "str": return "['str',%s]" % json.dumps(f[1])
    if t in ("time8", "len"): return "['%s']" % t
    raise ValueError(t)
def spec_fn(name, fields):
    body = SHEAD + "msg.spec=[" + ",".join(fjs(f) for f in fields) + "];\nreturn msg;"
    return add(dict(id=nid(), type="function", z=ZD, name=name, func=body, outputs=1, timeout=0,
                    noerr=0, initialize="", finalize="", libs=[], x=470, y=2400, wires=[[ENC]]))

H = lambda nt, ni: [("u8", nt), ("u8", ni), ("u8", 1), ("len",)]
A, N, T0, T1, T2, T3 = "APP", "NCAP", "T0", "T1", "T2", "T3"
# inline-builder-name -> spec fields (params taken from the D0C CSV equivalents)
SPECS = {
 "D0 NCAP discovery":       H(1,8)+[("uuid",A)],
 "D0 TIM discovery":        H(1,9)+[("uuid",A),("uuid",N)],
 "D0 XDCR discovery":       H(1,10)+[("uuid",A),("uuid",N),("uuid",T0)],
 "D0 Multi-ch read":        H(2,5)+[("uuid",A),("uuid",N),("u16",2),("uuida",[T0,T1]),("u16a",[1,1]),("u16a",[1,1]),("u8",5),("time8",)],
 "D0 Block read":           H(2,6)+[("uuid",A),("uuid",N),("u16",1),("uuida",[T0]),("u16a",[1]),("u16a",[1]),("u32",3),("time8",),("time8",),("time8",)],
 "D0 Event subscribe":      H(4,1)+[("uuid",A),("uuid",N),("uuid",T0),("u16",1),("str",""),("str","app0"),("time8",),("time8",)],
 "D0 Heartbeat subscribe":  H(4,10)+[("uuid",A),("time8",),("time8",)],
 "D0 Event unsubscribe":    H(4,3)+[("uuid",A),("uuid",N),("uuid",T0),("u16",1),("u16",0)],
 "D0 Heartbeat unsubscribe":H(4,12)+[("uuid",A)],
}
def feeders(nid_): return [m for m in j if any(nid_ in (w or []) for w in m.get("wires", []) or [])]

converted = 0
for old_name, fields in SPECS.items():
    ob = next((n for n in j if n.get("name") == old_name and n.get("z") == ZD), None)
    if not ob: continue
    sp = spec_fn(old_name + " [spec]", fields)
    for fb in feeders(ob["id"]):                       # repoint the button(s)
        fb["wires"] = [[ (sp if t == ob["id"] else t) for t in (w or [])] for w in fb["wires"]]
    converted += 1

# SERVO D0 BIN (gauge, dynamic value) -> spec that reads msg.payload
GAUGE = (SHEAD +
         "var v=parseInt(msg.payload,10);if(isNaN(v))v=0;v=Math.max(0,Math.min(100,v));\n"
         "msg.spec=[['u8',2],['u8',7],['u8',1],['len'],['uuid',APP],['uuid',NCAP],['uuid',T3],"
         "['u16',4],['u8',0],['str',''+v],['time8']];\nreturn msg;")
gb = add(dict(id=nid(), type="function", z=ZD, name="Gauge write [spec]", func=GAUGE, outputs=1,
              timeout=0, noerr=0, initialize="", finalize="", libs=[], x=470, y=2700, wires=[[ENC]]))
servo = next((n for n in j if n.get("name") == "SERVO D0 BIN" and n.get("z") == ZD), None)
if servo:
    for fb in feeders(servo["id"]):
        fb["wires"] = [[ (gb if t == servo["id"] else t) for t in (w or [])] for w in fb["wires"]]
    converted += 1

# delete the now-orphaned inline builders (no incoming after repoint)
j.extend(new)
ids2 = {n["id"] for n in j}
def inc(nid_): return any(nid_ in (w or []) for m in j for w in m.get("wires", []) or [])
to_del = set()
for nm in list(SPECS) + ["SERVO D0 BIN"]:
    ob = next((n for n in j if n.get("name") == nm and n.get("z") == ZD), None)
    if ob and not inc(ob["id"]):
        to_del.add(ob["id"])
j = [n for n in j if n["id"] not in to_del]
for n in j:
    if "wires" in n:
        n["wires"] = [[t for t in (w or []) if t not in to_del] for w in n["wires"]]
print("converted %d inline builders to spec; deleted %d old builders" % (converted, len(to_del)))

# validate + syntax
allids = {n["id"] for n in j}
for n in j:
    for w in n.get("wires", []) or []:
        for t in (w or []): assert t in allids, "dangling"
bad = 0
for n in new:
    if n.get("type") == "function":
        p = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False)
        p.write("var global={get:function(){return '';}};function f(msg){\n%s\n}" % n["func"]); p.close()
        r = subprocess.run(["node", "--check", p.name], capture_output=True, text=True); os.unlink(p.name)
        if r.returncode: print("SYNTAX", n["name"], r.stderr.splitlines()[-1]); bad += 1
assert bad == 0
# confirm: how many D0 builders now feed the encoder vs bypass it
enc_feeders = [m for m in j if any(ENC in (w or []) for w in m.get("wires", []) or [])]
print("D0 builders feeding the encoder now:", len(enc_feeders))

json.dump(j, open("NodeRED.json", "w"), ensure_ascii=False, indent=4)
try:
    body = json.dumps({"flows": j, "deploymentType": "full"}).encode()
    dreq = urllib.request.Request("http://127.0.0.1:1880/flows", data=body, method="POST",
        headers={"Content-Type": "application/json", "Node-RED-API-Version": "v2"})
    print("DEPLOY:", urllib.request.urlopen(dreq, timeout=30).status)
except Exception as e:
    print("DEPLOY FAILED:", repr(e)); sys.exit(1)
print("repo==live:", {n['id'] for n in json.load(urllib.request.urlopen(req, timeout=10))['flows']} == {n['id'] for n in j}, "| nodes", len(j))
