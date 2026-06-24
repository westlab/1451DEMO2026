#!/usr/bin/env python3
# Tidy the dashboard so widgets tile cleanly. Focus: Request-Response (demo tab) + fix the
# broken DASHBOARD empty groups + light TEDS fix. Leaves M5Core2 tab (already clean) alone.
import json, urllib.request, sys

SRC="NodeRED.json"
req=urllib.request.Request("http://127.0.0.1:1880/flows",headers={"Node-RED-API-Version":"v2"})
live=json.load(urllib.request.urlopen(req,timeout=10))["flows"]
repo_text=open(SRC).read(); j=json.loads(repo_text)
if {n["id"] for n in live}!={n["id"] for n in j}:
    print("ABORT: repo/live diverge"); sys.exit(1)
print("sync OK (%d nodes)"%len(j))

tabs={n["name"]:n["id"] for n in j if n.get("type")=="ui_tab"}
def groups_of(tab): return [n for n in j if n.get("type")=="ui_group" and n.get("tab")==tab]
def widgets(gid): return [n for n in j if n.get("group")==gid]
def setwh(w,wd,ht): w["width"],w["height"]=str(wd),str(ht)
def title(w): return (w.get("title") or "")

RR=tabs["Request-Response"]; DASH=tabs["DASHBOARD"]; TEDS=tabs["TEDS"]

# ---- group order on Request-Response: M5 first, then D0C block, then D0 block ----
RR_ORDER={"M5 #1  Req/Res (TIM3)":1,"M5 #2  Req/Res (TIM4)":2,
          "D0C Gauge":3,"D0C TEMP/HUMID":4,"D0C 1451.1.6 EXT":5,
          "D0 Gauge":6,"D0 TEMP/HUMID":7,"D0 1451.1.6 EXT":8}
for g in groups_of(RR):
    g["width"]="6"; g["order"]=RR_ORDER.get(g["name"],g.get("order",99))
    ws=widgets(g["id"]); nm=g["name"]
    if nm.startswith("M5 #") and "Req/Res" in nm:
        # 4 read buttons (2/row) | CO2 gauge + Gauge(actual) side by side | text | write slider
        btns=sorted([w for w in ws if w["type"]=="ui_button"],key=lambda z:z.get("order") or 0)
        for i,w in enumerate(btns,1): setwh(w,3,1); w["order"]=i
        for w in ws:
            if w["type"]=="ui_gauge" and "CO2" in title(w):     setwh(w,3,3); w["order"]=5
            elif w["type"]=="ui_gauge" and "actual" in title(w): setwh(w,3,3); w["order"]=6
            elif w["type"]=="ui_text":                           setwh(w,6,2); w["order"]=7
            elif w["type"]=="ui_slider":                         setwh(w,6,1); w["order"]=8
    elif nm.endswith("Gauge"):           # legacy D0C/D0 Gauge: slider on top, reacting gauge below
        for w in ws:
            if w["type"]=="ui_slider": setwh(w,6,1); w["order"]=1
            elif w["type"]=="ui_gauge": setwh(w,6,3); w["order"]=2
    elif "TEMP/HUMID" in nm:             # button+gauge pairs, 2 per row
        for w in sorted(ws,key=lambda z:z.get("order") or 0):
            if w["type"]=="ui_button": setwh(w,3,1)
            elif w["type"]=="ui_gauge": setwh(w,3,3)
    elif "EXT" in nm:                    # discovery buttons grid + chart at the end
        bi=1
        for w in sorted(ws,key=lambda z:z.get("order") or 0):
            if w["type"]=="ui_button": setwh(w,3,1); w["order"]=bi; bi+=1
            elif w["type"]=="ui_chart": setwh(w,6,5); w["order"]=99
print("Request-Response: groups reordered + widgets sized for clean tiling")

# ---- DASHBOARD: drop empty ghost groups, reorder real TEMP/HUMID ----
empty={g["id"] for g in groups_of(DASH) if len(widgets(g["id"]))==0}
if empty:
    j[:]=[n for n in j if n.get("id") not in empty]
    print("DASHBOARD: removed %d empty ghost groups"%len(empty))
o=1
for name in ("TEMP","HUMID"):
    for g in [x for x in j if x.get("type")=="ui_group" and x.get("tab")==DASH and x["name"]==name]:
        g["order"]=o; g["width"]="6"; o+=1
        for w in widgets(g["id"]):
            if w["type"]=="ui_gauge": setwh(w,6,4)
            elif w["type"]=="ui_chart": setwh(w,6,6)

# ---- TEDS: sequential buttons + keep text boxes ----
for g in groups_of(TEDS):
    g["width"]="6"; bi=1
    for w in sorted(widgets(g["id"]),key=lambda z:z.get("order") or 0):
        if w["type"]=="ui_button": setwh(w,3,1); w["order"]=bi; bi+=1
        elif w["type"]=="ui_text": w["width"]="6"; w["order"]=max(bi,20)
print("DASHBOARD + TEDS tidied")

assert len({n["id"] for n in j})==len(j)
json.dump(j,open(SRC,"w"),ensure_ascii=False,indent=4)
try:
    body=json.dumps({"flows":j,"deploymentType":"full"}).encode()
    dreq=urllib.request.Request("http://127.0.0.1:1880/flows",data=body,method="POST",
        headers={"Content-Type":"application/json","Node-RED-API-Version":"v2"})
    print("DEPLOY:",urllib.request.urlopen(dreq,timeout=30).status)
except Exception as e:
    open(SRC,"w").write(repo_text); print("DEPLOY FAILED, reverted:",repr(e)); sys.exit(1)
print("repo==live:",{n['id'] for n in json.load(urllib.request.urlopen(req,timeout=10))['flows']}=={n['id'] for n in j},"| nodes",len(j))
