#!/usr/bin/env python3
# Rebuild the "M5Core2" flow + dashboard to match the current environment:
#   m5iot/m5-01|m5-02 telemetry(JSON temp/humid/co2/gauge) + status + gauge command.
import json, sys

SRC = "NodeRED.json"
j = json.load(open(SRC))
byid = {n["id"]: n for n in j}

FLOW   = "9ac7a3893df687ef"   # tab "M5Core2" (flow canvas)
UITAB  = "f4ac53ae9cb8067e"   # ui_tab "M5Core2"
BROKER = "39d5aa93d6951ccb"   # broker.hivemq.com (already used everywhere)
GROUPS = {0: "3d28726f78ea7296", 1: "ab28081145731513"}  # reuse existing NODE-A / NODE-B groups

# ---- 1. strip every node living on the M5 flow canvas (old IECON plugfest flow) ----
removed = [n for n in j if n.get("z") == FLOW]
j = [n for n in j if n.get("z") != FLOW]
byid = {n["id"]: n for n in j}
print(f"removed {len(removed)} old M5-flow nodes")

# ---- id generator (16-char, guaranteed unique vs existing) ----
_ctr = [0]
def nid():
    while True:
        _ctr[0] += 1
        cand = ("c5%014x" % _ctr[0])[:16]
        if cand not in byid:
            byid[cand] = True
            return cand

new = []
def add(node):
    new.append(node)
    return node["id"]

UNITS = [
    dict(idx=0, dev="m5-01", unit="UNIT1", tim="TIM3", title="M5 #1  (UNIT1 / TIM3)"),
    dict(idx=1, dev="m5-02", unit="UNIT2", tim="TIM4", title="M5 #2  (UNIT2 / TIM4)"),
]

# ---- 2. (re)define the two dashboard groups ----
for u in UNITS:
    g = byid_orig = None
    gid = GROUPS[u["idx"]]
    grp = next((n for n in j if n.get("id") == gid), None)
    if grp is None:
        grp = {"id": gid, "type": "ui_group", "tab": UITAB}
        j.append(grp)
    grp.update(dict(type="ui_group", name=u["title"], tab=UITAB,
                    order=u["idx"] + 1, disp=True, width="6", collapse=False, className=""))

GREEN, YEL, RED = "#00b500", "#e6e600", "#ca3838"

def gauge(group, order, title, label, vmin, vmax, seg1="", seg2="", colors=None, x=0, y=0):
    return dict(id=nid(), type="ui_gauge", z=FLOW, name="", group=group, order=order,
                width="3", height="3", gtype="gage", title=title, label=label,
                format="{{value}}", min=vmin, max=vmax,
                colors=colors or [GREEN, YEL, RED], seg1=str(seg1), seg2=str(seg2),
                diff=False, className="", x=x, y=y, wires=[])

def chart(group, order, label, ymin="", ymax="", x=0, y=0):
    return dict(id=nid(), type="ui_chart", z=FLOW, name="", group=group, order=order,
                width="6", height="4", label=label, chartType="line", legend="true",
                xformat="HH:mm:ss", interpolate="linear", nodata="", dot=False,
                ymin=str(ymin), ymax=str(ymax), removeOlder="10", removeOlderPoints="",
                removeOlderUnit="60", cutout=0, useOneColor=False, useUTC=False,
                colors=["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
                        "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22"],
                outputs=1, useDifferentColor=False, className="", x=x, y=y, wires=[[]])

def text(group, order, label, x=0, y=0):
    return dict(id=nid(), type="ui_text", z=FLOW, group=group, order=order,
                width="6", height="1", name="", label=label, format="{{msg.payload}}",
                layout="row-spread", className="", x=x, y=y, wires=[])

def func(name, code, outputs=1, x=0, y=0, wires=None):
    return dict(id=nid(), type="function", z=FLOW, name=name, func=code, outputs=outputs,
                timeout=0, noerr=0, initialize="", finalize="", libs=[], x=x, y=y,
                wires=wires if wires is not None else [[] for _ in range(outputs)])

def slider(group, order, label, x=0, y=0, wires=None):
    return dict(id=nid(), type="ui_slider", z=FLOW, name="", label=label, tooltip="",
                group=group, order=order, width="6", height="1", passthru=True, outs="end",
                topic="payload", topicType="msg", min=0, max="100", step=1, className="",
                x=x, y=y, wires=wires or [[]])

def button(group, order, label, payload, x=0, y=0, wires=None):
    return dict(id=nid(), type="ui_button", z=FLOW, name="", group=group, order=order,
                width="2", height="1", passthru=True, label=label, tooltip="", color="",
                bgcolor="", className="", icon="", payload=str(payload), payloadType="str",
                topic="", topicType="str", x=x, y=y, wires=wires or [[]])

def mqtt_in(topic, datatype, x=0, y=0, wires=None):
    return dict(id=nid(), type="mqtt in", z=FLOW, name="", topic=topic, qos="0",
                datatype=datatype, broker=BROKER, nl=False, rap=False, rh=0, inputs=0,
                x=x, y=y, wires=wires or [[]])

def mqtt_out(topic, x=0, y=0):
    return dict(id=nid(), type="mqtt out", z=FLOW, name="", topic=topic, qos="0", retain="",
                respTopic="", contentType="", userProps="", correl="", expiry="",
                broker=BROKER, x=x, y=y, wires=[])

SPLIT = """// M5 telemetry JSON -> 4 series
var p = msg.payload || {};
function val(v){ return (typeof v === 'number' && !isNaN(v)) ? v : null; }
var t = val(p.temp), h = val(p.humid), c = val(p.co2), g = val(p.gauge);
return [
  t!==null ? {payload:t, topic:'TEMP'}  : null,
  h!==null ? {payload:h, topic:'HUMID'} : null,
  c!==null ? {payload:c, topic:'CO2'}   : null,
  g!==null ? {payload:g, topic:'GAUGE'} : null
];"""

STATUS = """var s = ('' + msg.payload).trim().toLowerCase();
msg.payload = (s === 'online') ? '🟢 ONLINE' : '🔴 OFFLINE (LWT)';
return msg;"""

GAUGEOUT = """// clamp 0-100 and send as plain text to M5
var v = parseInt(msg.payload, 10);
if (isNaN(v)) return null;
v = Math.max(0, Math.min(100, v));
msg.payload = '' + v;
return msg;"""

for u in UNITS:
    gid = GROUPS[u["idx"]]
    bx = 150
    by = 80 + u["idx"] * 520

    # widgets (defined first so we can wire to them)
    g_temp  = gauge(gid, 2, "温度 ℃",   "degC", "-10", "50",  x=720, y=by)
    g_humid = gauge(gid, 3, "湿度 %",    "%RH",  "0",   "100", x=720, y=by+50)
    g_co2   = gauge(gid, 4, "CO2 ppm",  "ppm",  "400", "2000", seg1="1000", seg2="1500", x=720, y=by+100)
    g_gauge = gauge(gid, 5, "ゲージ実値", "0-100","0",   "100", colors=["#0094ce","#0094ce","#0094ce"], x=720, y=by+150)
    ch_th   = chart(gid, 6, "温度/湿度 トレンド", x=720, y=by+210)
    ch_co2  = chart(gid, 7, "CO2 トレンド", x=720, y=by+270)
    st_txt  = text (gid, 1, u["dev"] + " 状態", x=720, y=by-30)
    for w in (st_txt, g_temp, g_humid, g_co2, g_gauge, ch_th, ch_co2):
        add(w)

    # telemetry path
    split = func("split " + u["dev"], SPLIT, outputs=4, x=440, y=by,
                 wires=[[g_temp["id"], ch_th["id"]],
                        [g_humid["id"], ch_th["id"]],
                        [g_co2["id"], ch_co2["id"]],
                        [g_gauge["id"]]])
    add(split)
    add(mqtt_in("m5iot/%s/telemetry" % u["dev"], "json", x=200, y=by, wires=[[split["id"]]]))

    # status path
    sfn = func("status " + u["dev"], STATUS, outputs=1, x=440, y=by+70, wires=[[st_txt["id"]]])
    add(sfn)
    add(mqtt_in("m5iot/%s/status" % u["dev"], "auto", x=200, y=by+70, wires=[[sfn["id"]]]))

    # actuator path (slider + presets -> clamp -> publish)
    mout = mqtt_out("m5iot/%s/gauge" % u["dev"], x=660, y=by+360)
    add(mout)
    gout = func("gauge->str " + u["dev"], GAUGEOUT, outputs=1, x=440, y=by+360, wires=[[mout["id"]]])
    add(gout)
    add(slider(gid, 8, "ゲージ操作 (0-100)", x=180, y=by+330, wires=[[gout["id"]]]))
    add(button(gid, 9,  "0",   0,   x=180, y=by+380, wires=[[gout["id"]]]))
    add(button(gid, 10, "50",  50,  x=260, y=by+380, wires=[[gout["id"]]]))
    add(button(gid, 11, "100", 100, x=340, y=by+380, wires=[[gout["id"]]]))

j.extend(new)
print(f"added {len(new)} new M5 nodes")

# ---- validation: unique ids, no dangling wires among flow nodes ----
ids = [n["id"] for n in j if isinstance(n, dict)]
assert len(ids) == len(set(ids)), "DUPLICATE IDS!"
allids = set(ids)
dangling = []
for n in j:
    for w in n.get("wires", []) or []:
        for tgt in (w or []):
            if tgt not in allids:
                dangling.append((n["id"], tgt))
assert not dangling, f"DANGLING WIRES: {dangling}"
# every ui widget points to an existing group
for n in j:
    if n.get("type", "").startswith("ui_") and n["type"] not in ("ui_base", "ui_tab", "ui_group") and "group" in n:
        assert n["group"] in allids, f"bad group ref {n['id']}"
print(f"OK: {len(j)} total nodes, ids unique, no dangling wires")

json.dump(j, open(SRC, "w"), ensure_ascii=False, indent=4)
print("written", SRC)
