#!/usr/bin/env python3
# Insert the zero-filled C-OP length column into every Node-RED C-OP builder /
# static button payload (CSV only). D0 binary, RR-Sync, monitor are untouched.
import json, urllib.request, sys, re

req = urllib.request.Request("http://127.0.0.1:1880/flows", headers={"Node-RED-API-Version": "v2"})
j = json.load(urllib.request.urlopen(req, timeout=10))["flows"]
print("base LIVE (%d nodes)" % len(j))

HEADER = re.compile(r"(=\s*[`'\"])(\d+,\d+,\d+,)")   # msg.payload=`t,i,m,  or  ='t,i,m,
def add_len_in_func(func):
    out = []; pos = 0; changed = 0
    for m in HEADER.finditer(func):
        out.append(func[pos:m.end()])
        rest = func[m.end():]
        if not rest.startswith("0,"):
            out.append("0,"); changed += 1
        pos = m.end()
    out.append(func[pos:])
    return "".join(out), changed

PAYHDR = re.compile(r"^(\d+,\d+,\d+,)(.*)$", re.S)
def add_len_in_payload(p):
    m = PAYHDR.match(p)
    if not m: return p, 0
    if m.group(2).startswith("0,"): return p, 0      # already has length col
    return m.group(1) + "0," + m.group(2), 1

fn_changed = pay_changed = 0
for n in j:
    if n.get("type") == "function" and isinstance(n.get("func"), str):
        # skip D0 binary spec builders / encoder / RR-sync / monitor (no CSV payload header)
        nf, c = add_len_in_func(n["func"])
        if c:
            n["func"] = nf; fn_changed += c
    elif n.get("type") == "ui_button" and n.get("payloadType") == "str":
        p = n.get("payload", "")
        if re.match(r"^\d+,\d+,\d+,", p):
            np, c = add_len_in_payload(p)
            if c:
                n["payload"] = np; pay_changed += c
print("inserted length col: %d in functions, %d in button payloads" % (fn_changed, pay_changed))

# validate function syntax for changed function nodes
import subprocess, tempfile, os
bad = 0
for n in j:
    if n.get("type") == "function" and "0," in n.get("func", "") and "msg.payload" in n.get("func", ""):
        pass
# quick node --check on all function nodes
for n in j:
    if n.get("type") == "function":
        p = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False)
        p.write("var global={get:function(){return '';}},flow={get:function(){return[];},set:function(){}},Buffer={from:function(x){return x;},isBuffer:function(){return false;}};function f(msg){\n%s\n}" % n["func"]); p.close()
        r = subprocess.run(["node", "--check", p.name], capture_output=True, text=True); os.unlink(p.name)
        if r.returncode:
            print("SYNTAX ERR", n.get("name"), r.stderr.splitlines()[-1]); bad += 1
print("function syntax errors:", bad); assert bad == 0

json.dump(j, open("NodeRED.json", "w"), ensure_ascii=False, indent=4)
try:
    body = json.dumps({"flows": j, "deploymentType": "full"}).encode()
    dreq = urllib.request.Request("http://127.0.0.1:1880/flows", data=body, method="POST",
        headers={"Content-Type": "application/json", "Node-RED-API-Version": "v2"})
    print("DEPLOY:", urllib.request.urlopen(dreq, timeout=30).status)
except Exception as e:
    print("DEPLOY FAILED:", repr(e)); sys.exit(1)
print("repo==live:", {n['id'] for n in json.load(urllib.request.urlopen(req, timeout=10))['flows']} == {n['id'] for n in j}, "| nodes", len(j))
