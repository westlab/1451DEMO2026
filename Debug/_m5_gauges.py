#!/usr/bin/env python3
# Give each M5 TIM group a full gauge set (TEMP/HUMID/CO2/GAUGE) instead of only
# a CO2 gauge, and rewire both reply decoders to route each channel to its gauge.
import json, urllib.request, subprocess, tempfile, os

req = urllib.request.Request("http://127.0.0.1:1880/flows", headers={"Node-RED-API-Version": "v2"})
j = json.load(urllib.request.urlopen(req, timeout=10))["flows"]
byid = {n["id"]: n for n in j}
ids = set(byid)
_c = [0]
def nid():
    while True:
        _c[0] += 1; cand = ("g5%014x" % _c[0])[:16]
        if cand not in ids: ids.add(cand); return cand
new = []

def widgets(gname):
    g = next(n for n in j if n.get("name") == gname)
    ws = [n for n in j if n.get("group") == g["id"]]
    co2 = next(n for n in ws if n.get("type") == "ui_gauge" and n.get("label") == "ppm")
    gau = next(n for n in ws if n.get("type") == "ui_gauge" and n.get("label") == "0-100")
    txt = next(n for n in ws if n.get("type") == "ui_text")
    return g, co2, gau, txt

def add_gauges(gname, tim):
    g, co2, gau, txt = widgets(gname)
    z = co2["z"]; gid = g["id"]
    temp = dict(id=nid(), type="ui_gauge", z=z, group=gid, order=5, width="3", height="3",
                gtype="gage", title="%s Temp" % tim, label="TEMP (K)", format="{{value}}",
                min="273", max="323", colors=["#2196f3", "#ff9800", "#f44336"], className="",
                x=900, y=co2.get("y", 200) - 80, wires=[])
    hum = dict(id=nid(), type="ui_gauge", z=z, group=gid, order=6, width="3", height="3",
               gtype="gage", title="%s Humid" % tim, label="HUMID (%)", format="{{value}}",
               min="0", max="100", colors=["#00b8d4", "#00b8d4", "#0091ea"], className="",
               x=900, y=co2.get("y", 200) - 40, wires=[])
    new.extend([temp, hum])
    # relabel + reorder existing gauges/text/slider
    co2["label"] = "CO2 (ppm)"; co2["order"] = 7
    gau["label"] = "GAUGE (0-100)"; gau["order"] = 8
    txt["order"] = 9
    for n in j:
        if n.get("group") == gid and n.get("type") == "ui_slider": n["order"] = 10
    return dict(temp=temp["id"], humid=hum["id"], co2=co2["id"], gauge=gau["id"], text=txt["id"])

m = {
    ("C-OP", "TIM3"): add_gauges("M5 TIM3 (C-OP)", "TIM3"),
    ("C-OP", "TIM4"): add_gauges("M5 TIM4 (C-OP)", "TIM4"),
    ("D0",   "TIM3"): add_gauges("M5 TIM3 (D0)", "TIM3"),
    ("D0",   "TIM4"): add_gauges("M5 TIM4 (D0)", "TIM4"),
}

NM = "var nm={1:'Temp',2:'Humid',3:'CO2',4:'Gauge'},un={1:'K',2:'%',3:'ppm',4:''};\n"
EX = "var ex=(ch===1)?' ('+( (typeof fv==='number'?fv:parseFloat(val)) -273.15).toFixed(1)+' \\u00b0C)':'';\n"

COP_FUNC = (
    "var s=(''+msg.payload).trim();var f=s.split(',');\n"
    "var NUL=[null,null,null,null,null,null,null,null,null,null];\n"
    "if(f.length<10||f[0]!=='2'||f[1]!=='1'||f[2]!=='2')return NUL;\n"
    "var tim=(f[7]||'').toLowerCase();var ch=parseInt(f[8],10);var fv=parseFloat(f[9]);var val=f[9];\n"
    + NM +
    "var ex=(ch===1)?' ('+(fv-273.15).toFixed(1)+' \\u00b0C)':'';\n"
    "var line='ch'+ch+' '+(nm[ch]||'?')+' = '+fv+' '+(un[ch]||'')+ex;\n"
    "var T='0x2400250026002700280029003a020f03',U='0x2400250026002700280029003a020f04';\n"
    "var base=(tim===T)?0:(tim===U)?5:-1;var out=NUL.slice();\n"
    "if(base<0)return out;\n"
    "if(ch>=1&&ch<=4)out[base+(ch-1)]={payload:fv};\n"
    "out[base+4]={payload:line};return out;")

D0_FUNC = (
    "var p=msg.payload;var NUL=[null,null,null,null,null,null,null,null,null,null];\n"
    "if(!Buffer.isBuffer(p)||p.length<66)return NUL;\n"
    "if(!(p[0]===2&&p[1]===1&&p[2]===2))return NUL;\n"
    "var timLast=p[54];var ch=(p[55]<<8)|p[56];\n"
    "var val=p.slice(57,p.length-8).toString('latin1').replace(/\\0/g,'');var fv=parseFloat(val);\n"
    + NM +
    "var ex=(ch===1)?' ('+(fv-273.15).toFixed(1)+' \\u00b0C)':'';\n"
    "var line='ch'+ch+' '+(nm[ch]||'?')+' = '+val+' '+(un[ch]||'')+ex;\n"
    "var base=(timLast===3)?0:(timLast===4)?5:-1;var out=NUL.slice();\n"
    "if(base<0)return out;\n"
    "if(ch>=1&&ch<=4)out[base+(ch-1)]={payload:fv};\n"
    "out[base+4]={payload:line};return out;")

def rewire(decoder_name, mode, func):
    d = next(n for n in j if n.get("name") == decoder_name)
    d["outputs"] = 10
    d["func"] = func
    t3, t4 = m[(mode, "TIM3")], m[(mode, "TIM4")]
    order = ["temp", "humid", "co2", "gauge", "text"]
    d["wires"] = [[t3[k]] for k in order] + [[t4[k]] for k in order]

rewire("M5 reply decode", "C-OP", COP_FUNC)
rewire("M5 D0 reply decode", "D0", D0_FUNC)

j.extend(new)
# validate
allids = {n["id"] for n in j}
for n in j:
    for w in n.get("wires", []) or []:
        for t in (w or []): assert t in allids, "dangling %s" % t
bad = 0
for nm_ in ("M5 reply decode", "M5 D0 reply decode"):
    n = next(x for x in j if x.get("name") == nm_)
    p = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False)
    p.write("var Buffer={isBuffer:function(){return false;}};function f(msg){\n%s\n}" % n["func"]); p.close()
    r = subprocess.run(["node", "--check", p.name], capture_output=True, text=True); os.unlink(p.name)
    if r.returncode: print("SYNTAX", nm_, r.stderr.splitlines()[-1]); bad += 1
assert bad == 0

json.dump(j, open("NodeRED.json", "w"), ensure_ascii=False, indent=4)
body = json.dumps({"flows": j, "deploymentType": "full"}).encode()
dreq = urllib.request.Request("http://127.0.0.1:1880/flows", data=body, method="POST",
    headers={"Content-Type": "application/json", "Node-RED-API-Version": "v2"})
print("DEPLOY:", urllib.request.urlopen(dreq, timeout=30).status, "| nodes", len(j))
g = next(n for n in j if n.get("name") == "M5 TIM3 (C-OP)")
print("\nM5 TIM3 (C-OP) widgets:")
for n in sorted([x for x in j if x.get("group") == g["id"]], key=lambda n: n.get("order", 0)):
    print("  o%-2s %-9s %r" % (n.get("order"), n.get("type"), n.get("label")))
