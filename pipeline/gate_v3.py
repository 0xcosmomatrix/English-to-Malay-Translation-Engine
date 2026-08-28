#!/usr/bin/env python3
"""Gate v3: pick the better of {draft, rewrite} by scoring BOTH against English.

v2 compared rewrite against draft, so it could only ever ask "did the rewrite break
something?" That locked in draft errors: block 19's draft wrote "15 000", the rewrite
repaired it to "15,000" matching the source, and v2 reverted the repair.

v3 scores each candidate against the English + the enforced rules and keeps the better
one (ties go to the rewrite, which is the register-improved text). The rewrite can now
REPAIR the draft, while still never landing a regression.
Gate-only: reuses saved draft/rewrite, so no re-drafting.
"""
import json,re,sys,os
import pipeline as P

MW={"0":"sifar","1":"satu","2":"dua","3":"tiga","4":"empat","5":"lima","6":"enam","7":"tujuh","8":"lapan","9":"sembilan"}
def unaccounted(en_b,t):
    """EN numbers with no counterpart in t, allowing 0-9 spelled out and % -> peratus."""
    import collections
    miss=list((collections.Counter(P.nums(en_b))-collections.Counter(P.nums(t))).elements())
    out=[]
    for n in miss:
        if n in MW and re.search(rf"(?<![\w-]){MW[n]}(?![\w-])",t,re.I): continue
        if n.endswith("%") and re.search(rf"{re.escape(n[:-1])}\s*(%|peratus)",t,re.I): continue
        out.append(n)
    return out

def has_word(t,w): return re.search(rf"(?<![\w-]){re.escape(w)}(?![\w-])",t,re.I) is not None
HARD={"aktivitas","jawaban","karena","akun","kebijakan","kualitas","fasilitas","universitas",
      "komunitas","napas","kantor","nomor","analisa","resiko","sistim","tehnik","ijin","merubah"}

def demerits(en_b,t):
    """Lower is better. Scored against English + enforced rules, never against a sibling draft."""
    d={}
    u=unaccounted(en_b,t)
    if u: d["facts"]=len(u)
    lost=[x for x in P.DNT if len(re.findall(rf"\b{re.escape(x)}\b",en_b))>len(re.findall(rf"\b{re.escape(x)}\b",t))]
    if lost: d["dnt"]=len(lost)
    tv=[v for v,_ in P.VARIANTS if has_word(t,v)]
    if tv: d["terms"]=len(tv)
    hi=[w for w in HARD if has_word(t,w)]
    if hi: d["indo"]=len(hi)
    return d
def total(d): return sum(d.values())

def run(name):
    cfg=P.CFG[name.split("@")[0]]
    en=P.split_blocks(open("en-ch01.md").read())
    dr=P.split_blocks(open(f"{name}-draft.md").read())
    rw=P.split_blocks(open(f"{name}-rewrite.md").read())
    assert len(en)==len(dr)==len(rw), f"block count mismatch {len(en)}/{len(dr)}/{len(rw)}"
    final=[];log=[];repaired=0
    for i,((k,e),(_,d),(_,r)) in enumerate(zip(en,dr,rw)):
        if k=="prot" or r.strip()==d.strip(): final.append((k,d)); continue
        dd,rd=demerits(e,d),demerits(e,r)
        if total(rd)>total(dd):
            final.append((k,d)); log.append({"block":i,"kept":False,"gate":"rules",
              "why":f"rewrite worse: draft={dd or 'clean'} rewrite={rd}"}); continue
        ok,why=P.meaning_gate(cfg["gate"],e,r)
        if ok and total(rd)<total(dd): repaired+=1; why=f"REPAIRED draft {dd} -> {rd}"
        final.append((k, r if ok else d))
        log.append({"block":i,"kept":ok,"gate":"meaning","why":why})
    open(f"{name}-final.md","w").write(P.join_blocks(final))
    ch=len(log);kept=sum(1 for l in log if l["kept"])
    json.dump({"gate":"v3","changed":ch,"kept":kept,"reverted":ch-kept,"repaired":repaired,"log":log},
              open(f"{name}-report.json","w"),ensure_ascii=False,indent=1)
    print(f"  [{name}] v3: changed={ch} kept={kept} reverted={ch-kept} draft-errors-repaired={repaired}")

for n in sys.argv[1:]: run(n)
