#!/usr/bin/env python3
# Pseudo TIM0/TIM1 are gone from NCAP. On the dashboard:
#  (1) remove the TEMP/HUMID display + single-read buttons from the Sensors group
#      (their data source no longer exists); keep the GAUGE actuator (TIM2).
#  (2) retarget every remaining builder that pointed at TIM0/TIM1 to the real
#      M5 units TIM3/TIM4, and fix the "· TIMx" labels.
import json, urllib.request, re, subprocess, tempfile, os

ZC, ZD = "0c7a731dc303bfda", "a4c183a95164db51"
req = urllib.request.Request("http://127.0.0.1:1880/flows", headers={"Node-RED-API-Version": "v2"})
j = json.load(urllib.request.urlopen(req, timeout=10))["flows"]
byid = {n["id"]: n for n in j}

# ---------- (1) Sensors group cleanup ----------
del_names = {"single read Temp [new]", "single read Humid [new]"}
del_ids = set()
for gname in ("D0C Sensors — TEMP/HUMID/GAUGE", "D0 Sensors — TEMP/HUMID/GAUGE"):
    g = next((n for n in j if n.get("name") == gname), None)
    if not g:
        continue
    g["name"] = gname.split(" Sensors")[0] + " GAUGE actuator (TIM2)"
    for w in [n for n in j if n.get("group") == g["id"]]:
        lab = w.get("label", "")
        if w.get("type") == "ui_gauge" and lab in ("TEMP (K)", "HUMID (%)"):
            del_ids.add(w["id"])
        if w.get("type") == "ui_button" and ("single read (Temp)" in lab or "single read (Humid)" in lab):
            del_ids.add(w["id"])
# the single-read builder functions
for n in j:
    if n.get("type") == "function" and n.get("name") in del_names:
        del_ids.add(n["id"])
# remove and scrub wires
j = [n for n in j if n["id"] not in del_ids]
for n in j:
    if "wires" in n:
        n["wires"] = [[t for t in (w or []) if t not in del_ids] for w in n["wires"]]
print("deleted %d Sensors widgets/builders (TEMP/HUMID + single reads)" % len(del_ids))

# ---------- (2) retarget remaining T0->T3, T1->T4 ----------
def retarget(f):
    f = f.replace("'uuid',T0", "'uuid',T3").replace("'uuid', T0", "'uuid', T3")
    f = f.replace("'uuid',T1", "'uuid',T4").replace("'uuid', T1", "'uuid', T4")
    f = f.replace("${T0}", "${T3}").replace("${T1}", "${T4}")
    f = f.replace("[T0,T1]", "[T3,T4]").replace("[T0]", "[T3]").replace("[T1]", "[T4]")
    return f
changed = 0
for n in j:
    if n.get("type") == "function" and n.get("z") in (ZC, ZD):
        f = n.get("func", "")
        nf = retarget(f)
        if nf != f:
            n["func"] = nf; changed += 1
print("retargeted %d builders T0->T3 / T1->T4" % changed)

# fix button labels
lab_chg = 0
for n in j:
    if n.get("type") == "ui_button" and n.get("z") in (ZC, ZD):
        lab = n.get("label", "")
        new = lab.replace("· TIM0+TIM1", "· TIM3+TIM4").replace("· TIM0", "· TIM3").replace("· TIM1", "· TIM4")
        if new != lab:
            n["label"] = new; lab_chg += 1
print("relabelled %d buttons" % lab_chg)

# ---------- validate ----------
allids = {n["id"] for n in j}
for n in j:
    for w in n.get("wires", []) or []:
        for t in (w or []):
            assert t in allids, "dangling %s" % t
# no remaining T0/T1 transducer refs in builders
leftover = []
for n in j:
    if n.get("type") == "function" and n.get("z") in (ZC, ZD):
        f = n.get("func", "")
        if re.search(r"'uuid',\s*T[01]\b", f) or "${T0}" in f or "${T1}" in f or "[T0" in f or "[T1" in f or ",T1]" in f:
            leftover.append(n.get("name"))
print("builders still referencing T0/T1:", leftover)
bad = 0
for n in j:
    if n.get("type") == "function" and n.get("z") in (ZC, ZD):
        p = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False)
        p.write("var global={get:function(){return '';}},Buffer={isBuffer:function(){return false;}};function f(msg){\n%s\n}" % n["func"]); p.close()
        r = subprocess.run(["node", "--check", p.name], capture_output=True, text=True); os.unlink(p.name)
        if r.returncode:
            print("SYNTAX", n.get("name"), r.stderr.splitlines()[-1]); bad += 1
assert bad == 0 and not leftover, "validation failed"

json.dump(j, open("NodeRED.json", "w"), ensure_ascii=False, indent=4)
body = json.dumps({"flows": j, "deploymentType": "full"}).encode()
dreq = urllib.request.Request("http://127.0.0.1:1880/flows", data=body, method="POST",
    headers={"Content-Type": "application/json", "Node-RED-API-Version": "v2"})
print("DEPLOY:", urllib.request.urlopen(dreq, timeout=30).status, "| nodes", len(j))
# show what now targets TIM3/TIM4 in former TIM0/TIM1 spots
for tn in ("Discovery", "Transducer", "Event"):
    for n in j:
        if n.get("type") == "ui_button" and n.get("z") == ZD and tn.lower() in (byid_g := {g["id"]: g.get("name", "") for g in j if g.get("type") == "ui_group"}).get(n.get("group"), "").lower():
            pass
