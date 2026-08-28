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
import json, sys, os as _os
sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import msml,re,sys,os,shutil,datetime,pathlib
HERE=pathlib.Path(__file__).resolve().parent
BL=HERE/"ms-indonesian-blocklist.json"
CAP=500   # throttle guardrail: the tier is meant to stay dozens-to-hundreds

def body(p):
    return msml.mask_body(pathlib.Path(p).read_text(encoding="utf8"))
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
    return msml.count_word(text,word)

# every rule-data file participates in drift detection, and file-set changes count
MANIFEST_GLOBS=("ms-*.json","en-idioms.json","corpus-registry.json")

def _manifest_files():
    files=[]
    for g in MANIFEST_GLOBS: files+=list(HERE.glob(g))
    return sorted(f for f in set(files) if f.name!="rules-manifest.json")

def stamp_manifest():
    """The one sanctioned stamp. Every legitimate writer (this gate, rulebook)
    calls it after writing, so honest edits never read as drift."""
    import hashlib
    man={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in _manifest_files()}
    tmp=str(HERE/"rules-manifest.json")+".tmp"
    open(tmp,"w").write(json.dumps(man,indent=1)); os.replace(tmp,str(HERE/"rules-manifest.json"))

def manifest_drift():
    mf=HERE/"rules-manifest.json"
    if not mf.exists(): return []
    import hashlib
    man=json.load(open(mf)); drift=[]
    cur={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in _manifest_files()}
    for fn in set(man)|set(cur):
        if man.get(fn)!=cur.get(fn): drift.append(fn)   # changed, missing, OR added
    return sorted(drift)

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
        o=led.get(w.lower(),{}).get("verdict","")
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
    if check:
        drift=manifest_drift()
        if drift:
            print(f"  MIRROR DRIFT: {drift} differ from the last stamped state — "
                  f"a hand-copy or unstamped edit has diverged; restamp or re-sync")
        sys.exit(1 if (demoted or drift) else 0)   # drift FAILS the check (review-2 find)
    if demoted or any("corpus_clean" not in e for e in bl.get("enforce",[])):
        shutil.copy(BL,str(BL)+".bak")
        bl["enforce"]=keep
        have={x["avoid_id"].lower() for x in bl["flag"]}
        for d in demoted:
            if d["avoid_id"].lower() not in have:
                bl["flag"].append({"en":d.get("en",""),"ms":d.get("ms",""),"avoid_id":d["avoid_id"],
                                   "demoted_from_enforce":d["demoted"],"demotion_why":d["why"]})
            else:
                # audit trail must survive even when the flag entry already exists
                for e in bl["flag"]:
                    if e["avoid_id"].lower()==d["avoid_id"].lower():
                        e["demoted_from_enforce"]=d["demoted"]; e["demotion_why"]=d["why"]
        bl["_counts"]={"enforce":len(keep),"flag":len(bl["flag"])}
        json.dump(bl,open(BL,"w"),ensure_ascii=False,indent=1)
        print("written (backup: .bak)")
    stamp_manifest()
    print("manifest stamped")
if __name__=="__main__":
    main()
