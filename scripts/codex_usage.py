"""Token usage of every Codex run (sums the `usage` blocks in each events_*.jsonl). Usage: python scripts/codex_usage.py <events folder>"""
import glob
import json
import os
import re
import sys

folder = sys.argv[1] if len(sys.argv) > 1 else "."
rows = []
for path in sorted(glob.glob(os.path.join(folder, "events_*.jsonl"))):
    fresh = cached = out = 0
    for line in open(path, encoding="utf-8", errors="replace"):
        for m in re.finditer(r'"usage":(\{[^}]*\})', line):
            u = json.loads(m.group(1))
            cached += u.get("cached_input_tokens", 0)
            fresh += u.get("input_tokens", 0) - u.get("cached_input_tokens", 0)
            out += u.get("output_tokens", 0)
    rows.append((os.path.basename(path)[7:-6], fresh, cached, out))
print(f"{'run':<10}{'new input':>12}{'cached input':>14}{'output':>10}")
for name, f, c, o in rows:
    print(f"{name:<10}{f:>12,}{c:>14,}{o:>10,}")
print(f"{'total':<10}{sum(r[1] for r in rows):>12,}{sum(r[2] for r in rows):>14,}{sum(r[3] for r in rows):>10,}")
