#!/usr/bin/env python3
# Split the single 'Request-Response' UI tab into two UI tabs: 'D0C' and 'D0'.
#  - reassign existing groups by mode (C-OP -> D0C tab, binary -> D0 tab)
#  - split the merged 'Monitor & Time-sync' group into per-tab copies
#  - build a NEW 'M5 Req/Res (D0 binary)' group on the D0 tab (TIM3+TIM4),
#    mirroring the C-OP M5 group but sending D0 binary via the shared encoder.
import json, urllib.request, subprocess, tempfile, os, sys

RR   = "86483328.1e604"          # current Request-Response UI tab -> becomes 'D0C'
ZC   = "0c7a731dc303bfda"        # D0C flow tab
ZD   = "a4c183a95164db51"        # D0 flow tab
ENC  = "ca00000000000001"        # D0 binary encode
BRK  = "39d5aa93d6951ccb"        # hivemq broker
SPFX = "_1451.1.6/"

req = urllib.request.Request("http://127.0.0.1:1880/flows", headers={"Node-RED-API-Version": "v2"})
j = json.load(urllib.request.urlopen(req, timeout=10))["flows"]
byid = {n["id"]: n for n in j}
ids = set(byid)
print("base LIVE (%d nodes)" % len(j))

_c = [0]
def nid():
    while True:
        _c[0] += 1; cand = ("d0%014x" % _c[0])[:16]
        if cand not in ids: ids.add(cand); return cand
new = []; add = lambda n: (new.append(n), n["id"])[1]

def grp(name):  # find group by exact name
    return next((n for n in j if n.get("type") == "ui_group" and n.get("name") == name), None)

# ---------- 1) UI tabs ----------
rrtab = byid[RR]; rrtab["name"] = "D0C"; rrtab["order"] = 2
D0TAB = add(dict(id=nid(), type="ui_tab", name="D0", icon="dashboard", order=3, disabled=False, hidden=False))
for t in j:
    if t.get("type") == "ui_tab" and t.get("name") == "TEDS": t["order"] = 4
print("renamed RR->D0C, created D0 tab", D0TAB)

# ---------- 2) reassign existing groups by mode ----------
D0C_GROUPS = ["M5 #1  Req/Res", "D0C TEMP/HUMID/GAUGE/EXT", "Async/Event svc (2,13-19)"]
D0_GROUPS  = ["D0 TEMP/HUMID/GAUGE/EXT", "Async/Event svc D0 (2,13-19)"]
for i, nm in enumerate(D0C_GROUPS, 1):
    g = grp(nm)
    if g: g["tab"] = RR;    g["order"] = i + 1   # M5 stays order 2; sensors 3; async 4
for i, nm in enumerate(D0_GROUPS, 1):
    g = grp(nm)
    if g: g["tab"] = D0TAB; g["order"] = i + 1   # sensors order 2; async 3 (M5 D0 will be order 1)
grp("M5 #1  Req/Res")["order"] = 1
print("reassigned groups: D0C<-%s | D0<-%s" % (D0C_GROUPS, D0_GROUPS))

# ---------- 3) split 'Monitor & Time-sync' into per-tab copies ----------
mon = grp("Monitor & Time-sync")
mon["name"] = "Monitor & Time-sync (D0C)"; mon["tab"] = RR; mon["order"] = 5
monD0 = add(dict(id=nid(), type="ui_group", name="Monitor & Time-sync (D0)", tab=D0TAB,
                 order=4, disp=True, width="12", collapse=True, className=""))
# move the three D0-side widgets (z == ZD) of the merged group into the new D0 group
moved = 0
for n in j:
    if n.get("group") == mon["id"] and n.get("z") == ZD:
        n["group"] = monD0; moved += 1
print("split monitor: moved %d D0-side widgets into new group %s" % (moved, monD0))

# ---------- 4) NEW 'M5 Req/Res (D0 binary)' group on the D0 tab ----------
G = add(dict(id=nid(), type="ui_group", name="M5 Req/Res (D0 binary)", tab=D0TAB,
             order=1, disp=True, width="6", collapse=False, className=""))

GH = ("var APP=global.get('APP'),NCAP=global.get('NCAP'),"
      "T3=global.get('T3'),T4=global.get('T4');\n")
# shared D0 binary READ builder: button payload 'tim,ch' (tim in {3,4}) -> sync_read spec
readb = add(dict(id=nid(), type="function", z=ZD, name="M5 D0 read build",
    func=GH + "var TIM={'3':T3,'4':T4};\nvar f=(''+msg.payload).split(',');\n"
         "var tim=TIM[f[0]],ch=parseInt(f[1],10);\nif(!tim)return null;\n"
         "msg.spec=[['u8',2],['u8',1],['u8',1],['len'],['uuid',APP],['uuid',NCAP],"
         "['uuid',tim],['u16',ch],['u8',5],['time8']];\nreturn msg;",
    outputs=1, timeout=0, noerr=0, initialize="", finalize="", libs=[], x=620, y=3040, wires=[[ENC]]))

# binary REPLY decoder: D0 reply on D0 topic -> 4 outs (TIM3 text/ppm, TIM4 text/ppm)
DEC = dict(id=nid(), type="function", z=ZD, name="M5 D0 reply decode",
    func=("var p=msg.payload;\n"
          "if(!Buffer.isBuffer(p)||p.length<66)return [null,null,null,null];\n"
          "if(!(p[0]===2&&p[1]===1&&p[2]===2))return [null,null,null,null];\n"
          "var timLast=p[54];           // last byte of timId (offset 39..54)\n"
          "var ch=(p[55]<<8)|p[56];\n"
          "var val=p.slice(57,p.length-8).toString('latin1').replace(/\\0/g,'');\n"
          "var fv=parseFloat(val);\n"
          "var nm={1:'Temp',2:'Humid',3:'CO2',4:'Gauge'},un={1:'K',2:'%',3:'ppm',4:''};\n"
          "var ex=(ch===1)?' ('+(fv-273.15).toFixed(1)+' \\u00b0C)':'';\n"
          "var line='ch'+ch+' '+(nm[ch]||'?')+' = '+val+' '+(un[ch]||'')+ex;\n"
          "var t={payload:line},gg={payload:fv};\n"
          "if(timLast===3)return [t, ch===3?gg:null, null, null];\n"
          "if(timLast===4)return [null, null, t, ch===3?gg:null];\n"
          "return [null,null,null,null];"),
    outputs=4, timeout=0, noerr=0, initialize="", finalize="", libs=[], x=640, y=3260, wires=[[],[],[],[]])
add(DEC)
add(dict(id=nid(), type="mqtt in", z=ZD, name="M5 D0 reply", topic=SPFX + "D0/PTTEST/ncap0",
         qos="0", datatype="buffer", broker=BRK, nl=False, rap=False, rh=0, inputs=0,
         x=420, y=3260, wires=[[DEC["id"]]]))

# per-TIM display widgets + their wiring to the decoder outputs
def gwrite_builder(tim_uuid_var, ch, name, y):
    return add(dict(id=nid(), type="function", z=ZD, name=name,
        func=GH + "var v=parseInt(msg.payload,10);if(isNaN(v))return null;v=Math.max(0,Math.min(100,v));\n"
             "msg.spec=[['u8',2],['u8',7],['u8',1],['len'],['uuid',APP],['uuid',NCAP],"
             "['uuid',%s],['u16',%d],['u8',0],['str',''+v],['time8']];\nreturn msg;" % (tim_uuid_var, ch),
        outputs=1, timeout=0, noerr=0, initialize="", finalize="", libs=[], x=620, y=y, wires=[[ENC]]))

def build_tim(tim_digit, tim_var, base_order, y0, out_text_idx, out_ppm_idx):
    # 4 read buttons
    for k,(cn,cl) in enumerate([(1,'Temp'),(2,'Humid'),(3,'CO2'),(4,'Gauge')]):
        add(dict(id=nid(), type="ui_button", z=ZD, group=G, order=base_order+k, width="3", height="1",
                 passthru=False, label="TIM%s %s read"%(tim_digit,cl), tooltip="", color="", bgcolor="",
                 className="", icon="", payload="%s,%d"%(tim_digit,cn), payloadType="str",
                 topic="", topicType="str", x=300, y=y0+k*40, wires=[[readb]]))
    # ppm gauge (fed by decoder)
    ppm = add(dict(id=nid(), type="ui_gauge", z=ZD, group=G, order=base_order+4, width="3", height="3",
                   gtype="gage", title="TIM%s CO2 ppm (D0)"%tim_digit, label="ppm", format="{{value}}",
                   min="400", max="2000", colors=["#00b500","#e6e600","#ca3838"], seg1="1000", seg2="1500",
                   className="", x=900, y=y0, wires=[]))
    # 0-100 gauge (fed by slider, commanded value)
    g100 = add(dict(id=nid(), type="ui_gauge", z=ZD, group=G, order=base_order+5, width="3", height="3",
                    gtype="gage", title="TIM%s Gauge (cmd)"%tim_digit, label="0-100", format="{{value}}",
                    min="0", max="100", colors=["#0094ce","#0094ce","#0094ce"], className="", x=900, y=y0+60, wires=[]))
    # last response text (fed by decoder)
    txt = add(dict(id=nid(), type="ui_text", z=ZD, group=G, order=base_order+6, width="6", height="2",
                   name="TIM%s Last response (D0)"%tim_digit, label="TIM%s Last response"%tim_digit,
                   format="{{msg.payload}}", layout="col-center", className="", x=900, y=y0+120, wires=[]))
    # gauge write slider -> 0-100 gauge (cmd echo) + D0 write builder
    wb = gwrite_builder(tim_var, 4, "M5 D0 gauge write %s"%tim_digit, y0+180)
    add(dict(id=nid(), type="ui_slider", z=ZD, group=G, order=base_order+7, width="6", height="1",
             label="TIM%s Gauge write (0-100)"%tim_digit, min=0, max="100", step=1, name="",
             className="", x=300, y=y0+180, wires=[[g100, wb]]))
    # wire decoder outputs to this TIM's text + ppm gauge
    DEC["wires"][out_text_idx] = [txt]
    DEC["wires"][out_ppm_idx]  = [ppm]

build_tim("3", "T3", 1, 3040, 0, 1)
build_tim("4", "T4", 9, 3480, 2, 3)
print("built new D0 M5 group %s (TIM3+TIM4)" % G)

# ---------- validate + deploy ----------
j.extend(new)
allids = {n["id"] for n in j}; byid2 = {n["id"]: n for n in j}
for n in j:
    for w in n.get("wires", []) or []:
        for t in (w or []): assert t in allids, "dangling %s" % t
cross = sum(1 for n in j if n.get("z") for w in (n.get("wires") or []) for t in (w or [])
            if byid2.get(t, {}).get("z") and byid2[t]["z"] != n["z"])
print("cross-tab wires:", cross); assert cross == 0
bad = 0
for n in new:
    if n.get("type") == "function":
        p = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False)
        p.write("var global={get:function(){return '';}},Buffer={isBuffer:function(){return false;}};"
                "function f(msg){\n%s\n}" % n["func"]); p.close()
        r = subprocess.run(["node", "--check", p.name], capture_output=True, text=True); os.unlink(p.name)
        if r.returncode: print("SYNTAX", n["name"], r.stderr.splitlines()[-1]); bad += 1
assert bad == 0, "syntax errors"

json.dump(j, open("NodeRED.json", "w"), ensure_ascii=False, indent=4)
body = json.dumps({"flows": j, "deploymentType": "full"}).encode()
dreq = urllib.request.Request("http://127.0.0.1:1880/flows", data=body, method="POST",
    headers={"Content-Type": "application/json", "Node-RED-API-Version": "v2"})
print("DEPLOY:", urllib.request.urlopen(dreq, timeout=30).status, "| nodes", len(j))
print("UI tabs:", [(n.get("name"), n.get("order")) for n in j if n.get("type") == "ui_tab"])
