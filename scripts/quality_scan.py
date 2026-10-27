#!/usr/bin/env python3
"""품질 스캔 — 활성 코드(app.py + routes/) 대상"""
import re, os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
targets = [os.path.join(BASE, "app.py")]
routes_dir = os.path.join(BASE, "routes")
for f in sorted(os.listdir(routes_dir)):
    if f.endswith(".py"):
        targets.append(os.path.join(routes_dir, f))

issues = {"bare_except": [], "silent_pass": [], "utcnow": [], "hardcoded_url": []}

for fpath in targets:
    if not os.path.exists(fpath):
        continue
    with open(fpath) as f:
        lines = f.readlines()
    short = os.path.relpath(fpath, BASE)
    for i, line in enumerate(lines, 1):
        s = line.strip()
        if s == "except:":
            issues["bare_except"].append(f"{short}:{i}")
        if re.match(r"^except\s+\w*:?\s*$", s) and i < len(lines):
            nxt = lines[i].strip() if i < len(lines) else ""
            if nxt == "pass":
                issues["silent_pass"].append(f"{short}:{i}: {s}")
        if "utcnow()" in line and "datetime" in line:
            issues["utcnow"].append(f"{short}:{i}")
        if re.search(r"http://localhost:\d{4}", line):
            if not any(x in line for x in ["#", "OLLAMA_BASE", "IMAC_OLLAMA", '= "http', "= 'http", "OLLAMA_BASE ="]):
                issues["hardcoded_url"].append(f"{short}:{i}: {s[:80]}")

total = 0
for k, v in issues.items():
    total += len(v)
    print(f"{k}: {len(v)}건")
    for item in v[:8]:
        print(f"  {item}")
    if len(v) > 8:
        print(f"  ... +{len(v)-8} more")
    print()

print(f"Total issues: {total}")
