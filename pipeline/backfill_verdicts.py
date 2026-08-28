#!/usr/bin/env python3
"""One-time backfill: convert this project's existing evaluation artifacts into
the labeled verdict log. Usage: backfill_verdicts.py <lab-dir>"""
import json, glob, sys, os, pathlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verdictlog as V

LAB = pathlib.Path(sys.argv[1])
n = {}
def add(kind, recs, source):
    V.log_verdicts(kind, recs, source); n[kind] = n.get(kind, 0) + len(recs)

# 1) blind panel votes (v2 and v3 runs + ch10 rematch)
for f, src in [(LAB/"panel-results.json","panel-v2-blind"), (LAB/"panel-v3-results.json","panel-v3-briefed")]:
    if not f.exists(): continue
    d = json.load(open(f))
    recs = []
    for s, out in zip(d["segments"], d["votes"]):
        for judge, v in out.items():
            if v.get("vote") in ("ship", "v2", "TIE"):
                recs.append({"label": v["vote"], "input": {"en": s["en"][:400], "a": s["ship"][:400],
                             "b": (s.get("v2new") or s["v2"])[:400]},
                             "meta": {"judge": judge, "reason": v.get("reason", "")[:200], "ch": s["ch"]}})
    add("panel-votes", recs, src)
# 2) sense-audit site decisions
f = LAB/"sense-audit.json"
if f.exists():
    recs = [{"label": s["decision"], "input": {"en": s["en"][:400], "ms": s["ms"][:200], "word": s["word"]},
             "meta": {"votes": s.get("votes"), "ch": s["ch"]}} for s in json.load(open(f))]
    add("sense-audit", recs, "site-audit")
# 3) per-block gate decisions from every book run's blocks.json
for bj in glob.glob(str(LAB/"t2-133-v3-full"/"*-blocks.json")) + glob.glob(str(LAB/"t2-133-v2-output"/"*-blocks.json")):
    recs = [{"label": e["gate"], "input": {"en": e["en"][:400], "draft": (e["draft"] or "")[:400],
             "rewrite": (e.get("rewrite") or "")[:400]}, "meta": {"why": e.get("why", "")[:200],
             "chapter": os.path.basename(bj)[:7], "block": e["i"]}}
            for e in json.load(open(bj)) if e.get("gate") and e["gate"] != "unchanged"]
    add("gate-decisions", recs, "backfill:" + os.path.basename(os.path.dirname(bj)))
# 4) dictionary ledger (already durable, but mirrored into the dataset shape)
led = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "rules", "ms-prpm-ledger.json")))
add("oracle-verdicts", [{"label": v["verdict"], "input": {"word": w},
                         "meta": {"evidence": v["evidence"][:140], "source_detail": v.get("source", "")}}
                        for w, v in led.items()], "prpm-ledger")
print("backfilled:", json.dumps(n, indent=1))
