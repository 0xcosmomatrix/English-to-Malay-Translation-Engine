#!/usr/bin/env python3
"""Targeted repair for DNT-loss and missing-fact residuals: re-translate ONLY the
flagged blocks with an explicit keep-verbatim instruction, accept a candidate only
when det_reasons strictly improves. Fail-safe: no improvement, no change.

Usage: repair_dnt.py <output-dir>
"""
import json, glob, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pipeline as P

def main():
    out = sys.argv[1]
    fixed = tried = 0
    for bj in sorted(glob.glob(os.path.join(out, "*-blocks.json"))):
        entries = json.load(open(bj))
        dirty = False
        for e in entries:
            if e["kind"] != "text":
                continue
            before = P.det_reasons(e["en"], e["final"])
            if not any("DNT lost" in x or "facts missing" in x for x in before):
                continue
            tried += 1
            keeps = sorted(set(re.findall(r"'([^']+)'", " ".join(x for x in before if "DNT lost" in x))))
            note = (" Keep these protected names EXACTLY as in the English, same number of times: "
                    + ", ".join(keeps) + "." if keeps else "")
            prompt = (P.draft_prompt() + note +
                      "\nAlso preserve every number exactly.\n\n" + P.numbered([e["en"]]))
            for attempt in range(2):
                try:
                    got = P.parse_numbered(P.call(P.CFG["budget"]["draft"], prompt,
                                                  temp=0.2 + 0.2 * attempt, stage="repair"), 1)
                except Exception:
                    continue
                if not got or not got[0]:
                    continue
                cand = P.apply_autofix(got[0])
                after = P.det_reasons(e["en"], cand)
                if len(after) < len(before):
                    print(f"  REPAIRED {os.path.basename(bj)[:20]} b{e['i']}: {before} -> {after or 'clean'}")
                    e["final"] = cand; e["final_det"] = after
                    dirty = True; fixed += 1
                    break
        if dirty:
            P.atomic_write(bj, json.dumps(entries, ensure_ascii=False, indent=1))
            P.atomic_write(bj.replace("-blocks.json", "-final.md"),
                           P.join_blocks([(x["kind"], x["final"]) for x in entries]))
            rp = bj.replace("-blocks.json", "-report.json")
            if os.path.exists(rp):
                rep = json.load(open(rp))
                res = [{"i": x["i"], "issues": P.det_reasons(x["en"], x["final"])}
                       for x in entries if x["kind"] == "text"]
                diag = [x for x in rep.get("residual_rule_issues", [])
                        if any("diagram field" in i for i in x.get("issues", []))]
                rep["residual_rule_issues"] = [x for x in res if x["issues"]] + diag
                rep["repaired_post_run"] = True
                P.atomic_write(rp, json.dumps(rep, ensure_ascii=False, indent=1))
    print(f"tried {tried}, repaired {fixed} block(s)")

if __name__ == "__main__":
    main()
