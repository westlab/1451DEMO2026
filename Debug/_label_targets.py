#!/usr/bin/env python3
# Make every Request-Response button self-explanatory: append the actual target
# transducer(s) to each label, e.g. "Async block read (2,13) · TIM3".
# The target is read from the button's builder function (the UUID globals it uses).
import json, urllib.request, re

req = urllib.request.Request("http://127.0.0.1:1880/flows", headers={"Node-RED-API-Version": "v2"})
j = json.load(urllib.request.urlopen(req, timeout=10))["flows"]
byid = {n["id"]: n for n in j}
TMAP = {"T0": "TIM0", "T1": "TIM1", "T2": "TIM2", "T3": "TIM3", "T4": "TIM4"}

def builder_of(btn):
    for w in btn.get("wires", []) or []:
        for t in (w or []):
            n = byid.get(t)
            if n and n.get("type") == "function":
                return n
    return None

def targets(func):
    if not func:
        return []
    toks = re.findall(r"\['uuid',\s*(T\d)\]", func)          # D0 spec single
    for arr in re.findall(r"uuida',\s*\[([^\]]*)\]", func):  # D0 spec multi
        toks += re.findall(r"T\d", arr)
    toks += re.findall(r"\$\{(T\d)\}", func)                 # C-OP single
    seen = []
    for t in toks:
        if t in TMAP and TMAP[t] not in seen:
            seen.append(TMAP[t])
    return seen

# service codes that are NCAP-scoped (no single transducer target)
NCAP_SCOPED = {(1, 8), (1, 9), (4, 10), (4, 12)}

def target_str(btn):
    lab = btn.get("label", "")
    bld = builder_of(btn)
    # M5 buttons: builder reads TIM from payload 'tim,ch'
    if bld and bld.get("name") == "M5 D0 read build":
        return None  # already 'TIM3 .../TIM4 ...' in label
    if bld and bld.get("name", "").startswith("Gauge write"):
        return None
    tl = targets(bld.get("func") if bld else "")
    m = re.search(r"\((\d+),(\d+)\)", lab)
    code = (int(m.group(1)), int(m.group(2))) if m else None
    if tl:
        return "+".join(tl)
    if code in NCAP_SCOPED:
        return "NCAP"
    if code == (1, 10):
        return None
    return None

def clean(lab):
    # drop redundant leading 'D0 ' / 'D0C ' (the tab already says the mode)
    lab = re.sub(r"^D0C?\s+", "", lab)
    # strip any previously-appended target
    lab = re.sub(r"\s+·\s+\S.*$", "", lab)
    return lab.strip()

changed = 0
for n in j:
    if n.get("type") != "ui_button":
        continue
    z = n.get("z")
    if z not in ("0c7a731dc303bfda", "a4c183a95164db51"):
        continue
    lab = n.get("label", "")
    if lab.startswith(("TIM3 ", "TIM4 ")):   # M5 buttons already clear
        continue
    base = clean(lab)
    tgt = target_str(n)
    new = base + (" · " + tgt if tgt else "")
    if new != lab:
        n["label"] = new; changed += 1

print("relabelled %d buttons" % changed)
json.dump(j, open("NodeRED.json", "w"), ensure_ascii=False, indent=4)
body = json.dumps({"flows": j, "deploymentType": "full"}).encode()
dreq = urllib.request.Request("http://127.0.0.1:1880/flows", data=body, method="POST",
    headers={"Content-Type": "application/json", "Node-RED-API-Version": "v2"})
print("DEPLOY:", urllib.request.urlopen(dreq, timeout=30).status)

# show resulting D0 tab labels grouped
tabs = {n["id"]: n.get("name") for n in j if n.get("type") == "ui_tab"}
tid = next(k for k, v in tabs.items() if v == "D0")
groups = {g["id"]: g.get("name") for g in j if g.get("type") == "ui_group" and g.get("tab") == tid}
for gid, gname in sorted(groups.items(), key=lambda kv: next(g.get("order", 0) for g in j if g["id"] == kv[0])):
    btns = [n for n in j if n.get("type") == "ui_button" and n.get("group") == gid]
    if not btns:
        continue
    print("\n[%s]" % gname)
    for b in sorted(btns, key=lambda n: n.get("order", 0)):
        print("   ", b.get("label"))
