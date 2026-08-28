#!/usr/bin/env python3
"""The ONLY sanctioned writer of the blocklist's enforce tier.

An entry may sit in `enforce` only while it holds all three legs:
  1. panel     - unanimous 3-family model panel said HARD_ERROR
  2. oracle    - PRPM ledger says VERIFIED_INDONESIAN or NO_ENTRY (never VALID_MALAY)
  3. corpus    - zero whole-word hits in every approved-malay corpus (text a human
                 reviewer read and shipped), zero collisions with english-source text
Any entry failing any leg is DEMOTED to the flag tier with the reason recorded —
enforcement fails toward advisory, never the other way.

Usage:  enforce_gate.py           apply (demote violators, stamp provenance)
        enforce_gate.py --check   validate only, exit 1 on violations (CI mode)
"""
import json,re,sys,os,shutil,datetime,pathlib
HERE=pathlib.Path(__file__).resolve().parent
BL=HERE/"ms-indonesian-blocklist.json"
CAP=500   # throttle guardrail: the tier is meant to stay dozens-to-hundreds

def body(p):
    t=re.sub(r"<!--.*?-->","",pathlib.Path(p).read_text(encoding="utf8"),flags=re.S)
    return re.sub(r"```.*?```","",t,flags=re.S)
def corpus_text(role,allow_missing=False):
    reg=json.load(open(HERE/"corpus-registry.json"))
    out=[]
    for c in reg["corpora"]:
        if c["role"]!=role: continue
        p=pathlib.Path(os.path.expanduser(c["path"]))
        if not p.is_absolute(): p=(HERE/p).resolve()
        files=[p] if p.is_file() else sorted(p.glob("*.md"))
        if not files and c.get("required",True) and not allow_missing:
            # Review finding (critical): a vanished corpus used to yield an empty
            # string, so every word passed leg 3 trivially. The gate fails closed.
            raise SystemExit(f"corpus missing: {c['path']} — leg 3 cannot run; "
                             f"populate corpus/ (see corpus/README.md) or pass --no-corpus (check mode only)")
        out+=[body(f) for f in files]
    return "\n".join(out)
def hits(word,text):
    return len(re.findall(rf"(?<![\w-]){re.escape(word)}(?![\w-])",text,re.I))

def main():
    check="--check" in sys.argv
    nocorpus="--no-corpus" in sys.argv
    if nocorpus and not check:
        raise SystemExit("--no-corpus is valid only with --check: never stamp corpus_clean on an untested tier")
    bl=json.load(open(BL)); led=json.load(open(HERE/"ms-prpm-ledger.json"))
    ms=corpus_text("approved-malay",allow_missing=nocorpus); en=corpus_text("english-source",allow_missing=nocorpus)
    if nocorpus and not (ms or en): print("NOTE: corpus leg SKIPPED (no corpora present) — legs 1-2 + provenance only")
    today=str(datetime.date.today()); keep=[]; demoted=[]
    for e in bl.get("enforce",[]):
        w=e["avoid_id"]; reasons=[]
        if e.get("panel")!="unanimous-3": reasons.append("no unanimous panel record")  # no default: absence of provenance is a failure, not a pass
        o=led.get(w.lower(),{}).get("verdict")
        if o not in ("VERIFIED_INDONESIAN","NO_ENTRY"): reasons.append(f"oracle leg fails: {o or 'not in ledger'}")
        h_ms,h_en=hits(w,ms),hits(w,en)
        if h_ms: reasons.append(f"fires {h_ms}x in approved Malay corpus")
        if h_en: reasons.append(f"collides {h_en}x with English source")
        if reasons:
            demoted.append({**e,"demoted":today,"why":"; ".join(reasons)})
        else:
            keep.append({**e,"panel":"unanimous-3","corpus_clean":today})
    print(f"enforce: {len(keep)} clean, {len(demoted)} demoted")
    for d in demoted: print(f"  DEMOTE {d['avoid_id']:<16} {d['why']}")
    if len(keep)>CAP: print(f"  WARNING: enforce tier {len(keep)} > {CAP} — review growth")
    if check: sys.exit(1 if demoted else 0)
    if demoted or any("corpus_clean" not in e for e in bl.get("enforce",[])):
        shutil.copy(BL,str(BL)+".bak")
        bl["enforce"]=keep
        have={x["avoid_id"].lower() for x in bl["flag"]}
        for d in demoted:
            if d["avoid_id"].lower() not in have:
                bl["flag"].append({"en":d.get("en",""),"ms":d.get("ms",""),"avoid_id":d["avoid_id"],
                                   "demoted_from_enforce":d["demoted"],"why":d["why"]})
        bl["_counts"]={"enforce":len(keep),"flag":len(bl["flag"])}
        json.dump(bl,open(BL,"w"),ensure_ascii=False,indent=1)
        print("written (backup: .bak)")
main()
