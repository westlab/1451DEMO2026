#!/usr/bin/env python3
# (1) fold reqTopicD0 into the working D0C set-global; delete the orphaned D0 set-global.
# (2) re-arrange D0-tab node positions to match D0C (button -> builder aligned, tidy columns).
import json, urllib.request, sys

ZD = "a4c183a95164db51"
D0C_SG = "00a1510000000017"     # D0C set global vars (triggered)
D0_SG = "00a1510000000002"      # D0 set global vars (orphaned)
REQ_D0 = "_1451.1.6/D0/PTTEST/ncap0"

req = urllib.request.Request("http://127.0.0.1:1880/flows", headers={"Node-RED-API-Version": "v2"})
j = json.load(urllib.request.urlopen(req, timeout=10))["flows"]
print("base LIVE (%d nodes)" % len(j))
byid = {n["id"]: n for n in j}

# ---- (1) global consolidation ----
sg = byid[D0C_SG]
if not any(r.get("p") == "reqTopicD0" for r in sg["rules"]):
    sg["rules"].append({"t": "set", "p": "reqTopicD0", "pt": "global", "to": REQ_D0, "tot": "str"})
    print("added reqTopicD0 to D0C set-global")
# delete orphaned D0 set-global (and any inject that ONLY fed it)
to_del = {D0_SG}
for n in list(j):
    if n.get("type") == "inject" and n.get("wires") and all(D0_SG in (w or []) and len(w) == 1 for w in n["wires"]):
        to_del.add(n["id"])
j = [n for n in j if n["id"] not in to_del]
# clean any wires pointing at deleted nodes
for n in j:
    if "wires" in n:
        n["wires"] = [[t for t in (w or []) if t not in to_del] for w in n["wires"]]
print("deleted orphaned node(s):", to_del)
byid = {n["id"]: n for n in j}

# ---- (2) D0 layout: align builders to buttons; tidy columns ----
BTN_X, BLD_X, ENC_X, OUT_X = 140, 470, 920, 1320
dnodes = [n for n in j if n.get("z") == ZD]
buttons = [n for n in dnodes if n.get("type") == "ui_button"]
buttons.sort(key=lambda n: n.get("y", 0))
positioned = set()

# re-space buttons evenly in a left column (keep order), 60px apart
y = 120
for b in buttons:
    b["x"] = BTN_X; b["y"] = y; positioned.add(b["id"]); y += 60
    # its immediate downstream function -> aligned at BLD_X, same y
    for w in b.get("wires", []) or []:
        for t in w:
            tg = byid.get(t)
            if tg and tg.get("z") == ZD and tg.get("type") == "function" and tg["id"] not in positioned:
                tg["x"] = BLD_X; tg["y"] = b["y"]; positioned.add(tg["id"])

# shared encoder + mqtt-outs to the right
enc = next((n for n in dnodes if n.get("name") == "D0 binary encode"), None)
if enc:
    enc["x"] = ENC_X; enc["y"] = 120; positioned.add(enc["id"])
oy = 120
for n in dnodes:
    if n.get("type") == "mqtt out":
        n["x"] = OUT_X; n["y"] = oy; positioned.add(n["id"]); oy += 80

# everything else on D0 (reply decoders, debug, toast, switch, template, mqtt in,
# THRU/ArrayJoin, orphaned *BIN builders) -> tidy grid in a lower "misc" band
misc = [n for n in dnodes if n["id"] not in positioned and n.get("type") in
        ("function", "switch", "change", "template", "debug", "ui_toast", "mqtt in", "json", "link in", "link out")]
miscy0 = y + 120
cols = [140, 470, 800, 1130, 1460]
for i, n in enumerate(misc):
    n["x"] = cols[i % len(cols)]; n["y"] = miscy0 + (i // len(cols)) * 70
print("arranged D0: %d buttons, %d misc nodes" % (len(buttons), len(misc)))

# validate (no dangling after deletion)
allids = {n["id"] for n in j}
for n in j:
    for w in n.get("wires", []) or []:
        for t in (w or []):
            assert t in allids, "dangling %s->%s" % (n["id"], t)
assert len({n["id"] for n in j}) == len(j)

json.dump(j, open("NodeRED.json", "w"), ensure_ascii=False, indent=4)
try:
    body = json.dumps({"flows": j, "deploymentType": "full"}).encode()
    dreq = urllib.request.Request("http://127.0.0.1:1880/flows", data=body, method="POST",
        headers={"Content-Type": "application/json", "Node-RED-API-Version": "v2"})
    print("DEPLOY:", urllib.request.urlopen(dreq, timeout=30).status)
except Exception as e:
    print("DEPLOY FAILED:", repr(e)); sys.exit(1)
print("repo==live:", {n['id'] for n in json.load(urllib.request.urlopen(req, timeout=10))['flows']} == {n['id'] for n in j}, "| nodes", len(j))
