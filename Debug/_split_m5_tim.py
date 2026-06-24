#!/usr/bin/env python3
# Make TIM3 vs TIM4 obvious: split each merged M5 group into two titled groups
# (one per TIM) and colour the read buttons (TIM3=blue, TIM4=orange).
import json, urllib.request

req = urllib.request.Request("http://127.0.0.1:1880/flows", headers={"Node-RED-API-Version": "v2"})
j = json.load(urllib.request.urlopen(req, timeout=10))["flows"]
byid = {n["id"]: n for n in j}
ids = set(byid)
_c = [0]
def nid():
    while True:
        _c[0] += 1; cand = ("d5%014x" % _c[0])[:16]
        if cand not in ids: ids.add(cand); return cand
new = []

BLUE, ORANGE, WHITE = "#1565c0", "#ef6c00", "#ffffff"

def tim_of(n):
    blob = " ".join(str(n.get(k, "")) for k in ("label", "title", "name"))
    return "TIM3" if "TIM3" in blob else "TIM4" if "TIM4" in blob else None

def split(old_name, t3name, t4name):
    g = next(n for n in j if n.get("name") == old_name)
    tab = g["tab"]
    g3 = dict(id=nid(), type="ui_group", name=t3name, tab=tab, order=1, disp=True, width="6",
              collapse=False, className=""); new.append(g3)
    g4 = dict(id=nid(), type="ui_group", name=t4name, tab=tab, order=2, disp=True, width="6",
              collapse=False, className=""); new.append(g4)
    n3 = n4 = 0
    for n in sorted([x for x in j if x.get("group") == g["id"]], key=lambda n: n.get("order", 0)):
        t = tim_of(n)
        if t == "TIM3":
            n3 += 1; n["group"] = g3["id"]; n["order"] = n3; col = BLUE
        else:
            n4 += 1; n["group"] = g4["id"]; n["order"] = n4; col = ORANGE
        if n.get("type") == "ui_button":
            n["bgcolor"] = col; n["color"] = WHITE
    # remove the now-empty merged group
    j[:] = [n for n in j if n["id"] != g["id"]]
    print("split %r -> %r(%d) + %r(%d)" % (old_name, t3name, n3, t4name, n4))

split("M5 #1  Req/Res",        "M5 TIM3 (C-OP)", "M5 TIM4 (C-OP)")
split("M5 Req/Res (D0 binary)", "M5 TIM3 (D0)",   "M5 TIM4 (D0)")

# bump the other groups on each tab so the two TIM groups sit first (orders 1,2)
TABS = {byid_tab["id"]: byid_tab.get("name")
        for byid_tab in j if byid_tab.get("type") == "ui_tab" and byid_tab.get("name") in ("D0C", "D0")}
ORD = {"D0C TEMP/HUMID/GAUGE/EXT": 3, "Async/Event svc (2,13-19)": 4, "Monitor & Time-sync (D0C)": 5,
       "D0 TEMP/HUMID/GAUGE/EXT": 3, "Async/Event svc D0 (2,13-19)": 4, "Monitor & Time-sync (D0)": 5}
for n in j:
    if n.get("type") == "ui_group" and n.get("name") in ORD and n.get("tab") in TABS:
        n["order"] = ORD[n["name"]]

j.extend(new)
# validate
allids = {n["id"] for n in j}
for n in j:
    for w in n.get("wires", []) or []:
        for t in (w or []): assert t in allids, "dangling %s" % t
assert len({n["id"] for n in j}) == len(j)

json.dump(j, open("NodeRED.json", "w"), ensure_ascii=False, indent=4)
body = json.dumps({"flows": j, "deploymentType": "full"}).encode()
dreq = urllib.request.Request("http://127.0.0.1:1880/flows", data=body, method="POST",
    headers={"Content-Type": "application/json", "Node-RED-API-Version": "v2"})
print("DEPLOY:", urllib.request.urlopen(dreq, timeout=30).status, "| nodes", len(j))
# show resulting group layout
from collections import Counter
wc = Counter(n.get("group") for n in j)
tabs = {n["id"]: n.get("name") for n in j if n.get("type") == "ui_tab"}
for tn in ("D0C", "D0"):
    tid = next(k for k, v in tabs.items() if v == tn)
    print("\n%s tab:" % tn)
    for g in sorted([n for n in j if n.get("type") == "ui_group" and n.get("tab") == tid], key=lambda n: n.get("order", 0)):
        print("  order=%s %-26r widgets=%d" % (g.get("order"), g.get("name"), wc.get(g["id"], 0)))
