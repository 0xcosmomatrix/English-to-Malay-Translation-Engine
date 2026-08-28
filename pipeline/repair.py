#!/usr/bin/env python3
"""P1 repair pass: mechanically apply context-free rulings to residual sites.

Selection gates can only choose between candidates; when draft AND rewrite both
carry an old form, it ships and is reported as a residual. This pass fixes those
sites deterministically — but ONLY for terms whose ruling is context-free
(autofix list below), in prose (never comments/fences), word-boundary matched,
and每 edit is re-verified: the block must be cleaner after than before or the
edit is rolled back.
Usage: repair.py <output-dir>
"""
import json,glob,os,re,sys,importlib.util
HERE=os.path.dirname(os.path.abspath(__file__))
spec=importlib.util.spec_from_file_location("p",os.path.join(HERE,"pipeline.py"))
os.environ.setdefault("OPENROUTER_API_KEY","dummy")
P=importlib.util.module_from_spec(spec); spec.loader.exec_module(P)
# Single source of truth: the termbase autofix flag, via the pipeline module.
# A private table here silently diverged from rulings once (review find).
AUTOFIX=dict(P.AUTOFIX)
CMT=re.compile(r"<!--.*?-->",re.S)
def swap_prose(text,v,c):
    """Replace in prose only: comments are lifted out, swapped text is case-adapted."""
    parts=[]; last=0
    for m in CMT.finditer(text):
        parts.append(("p",text[last:m.start()])); parts.append(("c",m.group(0))); last=m.end()
    parts.append(("p",text[last:]))
    def rep(mm):
        s=mm.group(0)
        if s.isupper() and len(s)>1: return c.upper()
        return (c[0].upper()+c[1:]) if s[0].isupper() and c[0].islower() else c
    return "".join(seg if kind=="c" else re.sub(rf"(?<![\w-]){re.escape(v)}(?![\w-])",rep,seg,flags=re.I)
                   for kind,seg in parts)
def main():
    out=sys.argv[1]; fixed=0
    for bj in sorted(glob.glob(os.path.join(out,"*-blocks.json"))):
        entries=json.load(open(bj)); dirty=False
        for e in entries:
            if e["kind"]!="text": continue
            before=P.det_reasons(e["en"],e["final"])
            if not any("term variant" in x for x in before): continue
            new=e["final"]
            for v,c in AUTOFIX.items():
                new=swap_prose(new,v,c)
            after=P.det_reasons(e["en"],new)
            if len(after)<len(before):
                print(f"  REPAIRED {os.path.basename(bj)[:7]} b{e['i']}: {before} -> {after or 'clean'}")
                e["final"]=new; dirty=True; fixed+=1
            elif new!=e["final"]:
                print(f"  ROLLBACK {os.path.basename(bj)[:7]} b{e['i']}: edit did not improve ({after})")
        if dirty:
            P.atomic_write(bj, json.dumps(entries,ensure_ascii=False,indent=1))
            P.atomic_write(bj.replace("-blocks.json","-final.md"),
                           "\n\n".join(x["final"] for x in entries)+"\n")
    print(f"repaired {fixed} block(s)")
main()
