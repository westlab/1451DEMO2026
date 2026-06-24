#!/usr/bin/env python3
# Every single-transducer M5 service currently targets TIM3 only. Add a TIM4
# sibling (builder + button) for each, so TIM3 and TIM4 are both operable.
import json, urllib.request, re

req = urllib.request.Request("http://127.0.0.1:1880/flows", headers={"Node-RED-API-Version": "v2"})
j = json.load(urllib.request.urlopen(req, timeout=10))["flows"]
byid = {n["id"]: n for n in j}
ids = set(byid)
_c = [0]
def nid():
    while True:
        _c[0] += 1; cand = ("t4%014x" % _c[0])[:16]
        if cand not in ids: ids.add(cand); return cand
new = []

ZS = ("0c7a731dc303bfda", "a4c183a95164db51")
def uses(f, t): return bool(re.search(r"\['uuid',\s*%s\]" % t, f or "")) or ("${%s}" % t in (f or ""))

# single-T3 service builders (exclude the gauge-actuator builders -> M5 groups handle those)
builders = [n for n in j if n.get("type") == "function" and n.get("z") in ZS
            and uses(n.get("func", ""), "T3") and not uses(n.get("func", ""), "T4")
            and "auge" not in n.get("name", "")]

def feeders(bid):
    return [m for m in j if m.get("type") == "ui_button" and any(bid in (w or []) for w in m.get("wires", []) or [])]

def t4_func(f):
    f = f.replace("['uuid',T3]", "['uuid',T4]").replace("['uuid', T3]", "['uuid',T4]")
    f = f.replace("${T3}", "${T4}")
    return f

added = 0
for b in builders:
    btns = feeders(b["id"])
    if not btns:
        continue
    # clone builder -> T4
    nb = dict(b); nb["id"] = nid(); nb["name"] = b.get("name", "") + " [T4]"
    nb["func"] = t4_func(b.get("func", "")); nb["y"] = (b.get("y", 0) or 0) + 20
    nb["wires"] = [list(w) for w in b.get("wires", [])]
    new.append(nb)
    for ob in btns:
        # make sure the TIM3 original is labelled · TIM3
        if "· TIM" not in ob.get("label", ""):
            ob["label"] = ob.get("label", "") + " · TIM3"
        else:
            ob["label"] = re.sub(r"· TIM\d", "· TIM3", ob["label"])
        # clone button -> TIM4
        nbtn = dict(ob); nbtn["id"] = nid()
        nbtn["label"] = re.sub(r"· TIM\d", "· TIM4", ob["label"])
        nbtn["wires"] = [[nb["id"]]]
        nbtn["order"] = (ob.get("order", 0) or 0) + 0.5
        nbtn["y"] = (ob.get("y", 0) or 0) + 20
        new.append(nbtn)
        added += 1

print("added %d TIM4 siblings (builders+buttons)" % added)
j.extend(new)

# renumber button orders within each affected group to clean ints (keep relative order)
groups = {n.get("group") for n in new if n.get("type") == "ui_button"}
for gid in groups:
    ws = sorted([n for n in j if n.get("group") == gid], key=lambda n: n.get("order", 0))
    for i, n in enumerate(ws, 1):
        n["order"] = i

# validate
allids = {n["id"] for n in j}
for n in j:
    for w in n.get("wires", []) or []:
        for t in (w or []): assert t in allids, "dangling %s" % t
import subprocess, tempfile, os
bad = 0
for n in new:
    if n.get("type") == "function":
        p = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False)
        p.write("var global={get:function(){return '';}};function f(msg){\n%s\n}" % n["func"]); p.close()
        r = subprocess.run(["node", "--check", p.name], capture_output=True, text=True); os.unlink(p.name)
        if r.returncode: print("SYNTAX", n["name"], r.stderr.splitlines()[-1]); bad += 1
assert bad == 0

json.dump(j, open("NodeRED.json", "w"), ensure_ascii=False, indent=4)
body = json.dumps({"flows": j, "deploymentType": "full"}).encode()
dreq = urllib.request.Request("http://127.0.0.1:1880/flows", data=body, method="POST",
    headers={"Content-Type": "application/json", "Node-RED-API-Version": "v2"})
print("DEPLOY:", urllib.request.urlopen(dreq, timeout=30).status, "| nodes", len(j))

# show D0 service groups now
tabs = {n["id"]: n.get("name") for n in j if n.get("type") == "ui_tab"}
tid = next(k for k, v in tabs.items() if v == "D0")
for gn in ("D0 Transducer access (2,x read/write)", "D0 Async access (2,13-19)", "D0 Event / Heartbeat (4,x)"):
    g = next(n for n in j if n.get("name") == gn)
    print("\n[%s]" % gn)
    for b in sorted([n for n in j if n.get("type") == "ui_button" and n.get("group") == g["id"]], key=lambda n: n.get("order", 0)):
        print("   ", b.get("label"))
