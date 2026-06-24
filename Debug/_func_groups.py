#!/usr/bin/env python3
# Reorganise the two big mixed groups on each of the D0C / D0 tabs into
# clear, function-titled groups (by IEEE 1451.0 service type) and give every
# display widget a descriptive label + colour-code buttons by category.
import json, urllib.request, re

req = urllib.request.Request("http://127.0.0.1:1880/flows", headers={"Node-RED-API-Version": "v2"})
j = json.load(urllib.request.urlopen(req, timeout=10))["flows"]
byid = {n["id"]: n for n in j}
ids = set(byid)
_c = [0]
def nid():
    while True:
        _c[0] += 1; cand = ("fg%014x" % _c[0])[:16]
        if cand not in ids: ids.add(cand); return cand
new = []

# category -> (title suffix, button colour)
CAT = {
    "sensors": ("Sensors — TEMP/HUMID/GAUGE", "#1565c0"),
    "disc":    ("Discovery (1,x)",                "#6a1b9a"),
    "xducer":  ("Transducer access (2,x read/write)", "#2e7d32"),
    "async":   ("Async access (2,13-19)",         "#00838f"),
    "event":   ("Event / Heartbeat (4,x)",        "#e65100"),
    "log":     ("Message log",                    None),
}
CAT_ORDER = ["sensors", "disc", "xducer", "async", "event", "log"]
WHITE = "#ffffff"

def classify(n):
    t = n.get("type"); lab = n.get("label") or ""; ttl = n.get("title") or ""; nm = n.get("name") or ""
    if t == "ui_template" and "msg log" in nm.lower(): return "log"
    if t in ("ui_gauge", "ui_slider"): return "sensors"
    if t == "ui_chart": return "event"          # 'event notify' chart
    m = re.search(r"\((\d+),(\d+)\)", lab)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if a == 1: return "disc"
        if a == 4: return "event"
        if a == 2:
            if b == 1: return "sensors"
            if 13 <= b <= 19: return "async"
            return "xducer"
    return "xducer"

def relabel_gauge(n):
    ttl = (n.get("title") or "")
    if "TEMP" in ttl.upper():  n["label"] = "TEMP (K)"
    elif "HUMID" in ttl.upper(): n["label"] = "HUMID (%)"
    elif n.get("label") == "0-100": n["label"] = "GAUGE (0-100)"
    if n.get("type") == "ui_slider" and n.get("label") == "Gauge":
        n["label"] = "GAUGE write (0-100)"

def reorg(tab_id, src_group_names, prefix):
    src_ids = [g["id"] for g in j if g.get("type") == "ui_group" and g.get("name") in src_group_names]
    widgets = [n for n in j if n.get("group") in src_ids]
    # create the function groups (only those that will receive widgets)
    buckets = {c: [] for c in CAT_ORDER}
    for w in widgets:
        buckets[classify(w)].append(w)
    gmap = {}
    for gi, c in enumerate([c for c in CAT_ORDER if buckets[c]], start=3):  # orders 3..; M5=1,2
        g = dict(id=nid(), type="ui_group", name="%s %s" % (prefix, CAT[c][0]), tab=tab_id,
                 order=gi, disp=True, width="6", collapse=False, className="")
        new.append(g); gmap[c] = g["id"]
    # assign widgets
    for c in CAT_ORDER:
        for oi, w in enumerate(sorted(buckets[c], key=lambda n: n.get("order", 0)), start=1):
            w["group"] = gmap[c]; w["order"] = oi
            if w.get("type") in ("ui_gauge", "ui_slider"): relabel_gauge(w)
            if w.get("type") == "ui_button" and CAT[c][1]:
                w["bgcolor"] = CAT[c][1]; w["color"] = WHITE
    # delete old groups
    j[:] = [n for n in j if n["id"] not in src_ids]
    # push Monitor group to the end
    for n in j:
        if n.get("type") == "ui_group" and n.get("tab") == tab_id and "Monitor" in (n.get("name") or ""):
            n["order"] = 20
    print("%s tab: %d widgets -> %s" % (prefix, len(widgets),
          {c: len(buckets[c]) for c in CAT_ORDER if buckets[c]}))

D0C_TAB = "86483328.1e604"
D0_TAB = next(n["id"] for n in j if n.get("type") == "ui_tab" and n.get("name") == "D0")
reorg(D0C_TAB, ["D0C TEMP/HUMID/GAUGE/EXT", "Async/Event svc (2,13-19)"], "D0C")
reorg(D0_TAB,  ["D0 TEMP/HUMID/GAUGE/EXT", "Async/Event svc D0 (2,13-19)"], "D0")

j.extend(new)
# validate
allids = {n["id"] for n in j}
for n in j:
    for w in n.get("wires", []) or []:
        for t in (w or []): assert t in allids, "dangling %s" % t
# every widget still has a valid group
gids = {n["id"] for n in j if n.get("type") == "ui_group"}
orphan = [n["id"] for n in j if n.get("group") and n.get("group") not in gids]
assert not orphan, "orphan widgets: %s" % orphan

json.dump(j, open("NodeRED.json", "w"), ensure_ascii=False, indent=4)
body = json.dumps({"flows": j, "deploymentType": "full"}).encode()
dreq = urllib.request.Request("http://127.0.0.1:1880/flows", data=body, method="POST",
    headers={"Content-Type": "application/json", "Node-RED-API-Version": "v2"})
print("DEPLOY:", urllib.request.urlopen(dreq, timeout=30).status, "| nodes", len(j))
from collections import Counter
wc = Counter(n.get("group") for n in j)
tabs = {n["id"]: n.get("name") for n in j if n.get("type") == "ui_tab"}
for tn in ("D0C", "D0"):
    tid = next(k for k, v in tabs.items() if v == tn)
    print("\n%s tab:" % tn)
    for g in sorted([n for n in j if n.get("type") == "ui_group" and n.get("tab") == tid], key=lambda n: n.get("order", 0)):
        print("  order=%-2s %-40r widgets=%d" % (g.get("order"), g.get("name"), wc.get(g["id"], 0)))
