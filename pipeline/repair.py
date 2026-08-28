#!/usr/bin/env python3
"""LEGACY repair tool: re-apply current context-free rulings to an EXISTING
output directory. Current runs no longer need it — gate() applies autofix to
both candidates before selection — but output produced before a ruling landed
can be brought up to date here. Engine and table both come from the pipeline:
this file owns no substitution logic of its own (a private copy diverged once).

Usage: repair.py <output-dir>
"""
import json, glob, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pipeline as P


def main():
    out = sys.argv[1]
    fixed = 0
    for bj in sorted(glob.glob(os.path.join(out, "*-blocks.json"))):
        entries = json.load(open(bj))
        dirty = False
        for e in entries:
            if e["kind"] != "text":
                continue
            before = P.det_reasons(e["en"], e["final"])
            if not any("term variant" in x for x in before):
                continue
            new = P.apply_autofix(e["final"])
            after = P.det_reasons(e["en"], new)
            if len(after) < len(before):
                print(f"  REPAIRED {os.path.basename(bj)[:7]} b{e['i']}: {before} -> {after or 'clean'}")
                e["final"] = new; e["final_det"] = after   # stashed verdicts must track the edit
                dirty = True; fixed += 1
            elif new != e["final"]:
                print(f"  ROLLBACK {os.path.basename(bj)[:7]} b{e['i']}: edit did not improve ({after})")
        if dirty:
            P.atomic_write(bj, json.dumps(entries, ensure_ascii=False, indent=1))
            P.atomic_write(bj.replace("-blocks.json", "-final.md"),
                           P.join_blocks([(e["kind"], e["final"]) for e in entries]))
            rp = bj.replace("-blocks.json", "-report.json")
            if os.path.exists(rp):
                rep = json.load(open(rp))
                res = [{"i": e["i"], "issues": P.det_reasons(e["en"], e["final"])}
                       for e in entries if e["kind"] == "text"]
                # diagram fail-to-source entries live in comments — det_reasons on the
                # block cannot re-derive them, so they must survive the refresh
                diag = [x for x in rep.get("residual_rule_issues", [])
                        if any("diagram field" in i for i in x.get("issues", []))]
                rep["residual_rule_issues"] = [x for x in res if x["issues"]] + diag
                rep["repaired_post_run"] = True
                P.atomic_write(rp, json.dumps(rep, ensure_ascii=False, indent=1))
    print(f"repaired {fixed} block(s)")


if __name__ == "__main__":
    main()
