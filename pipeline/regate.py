#!/usr/bin/env python3
"""Re-run rewrite + CORRECTED gate from a saved draft. No re-drafting.

Gate fix (v2), from the budget run's evidence:
- ms-indonesian-blocklist.json is FLAG-ONLY by its own contract. v1 used all 1070
  entries as hard-revert triggers, which reverted 10 good rewrites over the word
  "bagi" (= "for"); the blocklist entry means "write bahagi when you mean DIVIDE".
  Now: blocklist is a reported metric, never a revert trigger.
- Only HARD_INDO — forms that are not Malay words at all — hard-reverts.
- ms-terms.json variants DO hard-revert (proven correct: caught piawaian/rangka kerja),
  now with word-boundary matching instead of substring.
- Meaning gate retries once before failing closed.
"""
import json,re,sys,time
import pipeline as P

# Curated, deliberately small: Indonesian forms with no Standard Malay reading.
# Kept conservative on purpose — anything ambiguous belongs in flag-only reporting.
HARD_INDO = {"aktivitas","jawaban","karena","akun","kebijakan","kualitas","kuantitas",
 "fasilitas","universitas","komunitas","napas","risiko-nya","izin","surat kabar","kantor",
 "nomor","februari","november","apotek","praktek","obyek","subyek","analisa","aktifitas",
 "resiko","standarisasi","sistim","managemen","tehnik","ijin","propinsi","jadual-nya",
 "silahkan","mempengaruhi","merubah","ketrampilan","pikir","bicara","kerja sama-nya"}

def wordset(s): return set(re.findall(r"[a-zA-ZÀ-ɏ']+", s.lower()))
def has_word(t,w): return re.search(rf"(?<![\w-]){re.escape(w)}(?![\w-])", t, re.I) is not None

def det_gate_v2(draft_b, rw_b):
    reasons=[]
    if P.nums(draft_b)!=P.nums(rw_b): reasons.append(f"numbers changed {P.nums(draft_b)}->{P.nums(rw_b)}")
    for d in P.DNT:
        if len(re.findall(rf"\b{re.escape(d)}\b",draft_b))>len(re.findall(rf"\b{re.escape(d)}\b",rw_b)):
            reasons.append(f"DNT lost: {d}")
    new=wordset(rw_b)-wordset(draft_b)
    hard=[w for w in new if w in HARD_INDO]
    if hard: reasons.append(f"hard Indonesian form: {sorted(hard)}")
    for v,canon in P.VARIANTS:                      # enforced termbase — word-boundary
        if has_word(rw_b,v) and not has_word(draft_b,v):
            reasons.append(f"term variant '{v}' (canonical: {canon})")
    return reasons

def meaning_gate_v2(model,en_b,rw_b):
    for _ in range(2):
        ok,reason=P.meaning_gate(model,en_b,rw_b)
        if reason!="unparseable gate response": return ok,reason
        time.sleep(2)
    return False,"unparseable gate response (2 tries)"

def blocklist_density(text):
    ws=wordset(text); return sorted(w for w in ws if w in P.FLAGSET)

def regate(name):
    cfg=P.CFG[name.split("@")[0]]
    en_blocks=P.split_blocks(open("en-ch01.md").read())
    dr_blocks=P.split_blocks(open(f"{name}-draft.md").read())
    if len(en_blocks)!=len(dr_blocks):
        print(f"  [{name}] block mismatch en={len(en_blocks)} draft={len(dr_blocks)} — realigning by kind")
    rw_blocks=P.do_rewrite(cfg["rewrite"],dr_blocks)
    open(f"{name}-rewrite.md","w").write(P.join_blocks(rw_blocks))     # save intermediate this time
    log=[];final=[]
    for i,((k,d_b),(_,r_b)) in enumerate(zip(dr_blocks,rw_blocks)):
        en_b=en_blocks[i][1] if i<len(en_blocks) else ""
        if k=="prot" or r_b.strip()==d_b.strip(): final.append((k,d_b)); continue
        det=det_gate_v2(d_b,r_b)
        if det: final.append((k,d_b)); log.append({"block":i,"kept":False,"gate":"det","why":";".join(det)}); continue
        ok,why=meaning_gate_v2(cfg["gate"],en_b,r_b)
        final.append((k, r_b if ok else d_b))
        log.append({"block":i,"kept":ok,"gate":"meaning","why":why})
    open(f"{name}-final.md","w").write(P.join_blocks(final))
    ch=len(log); kept=sum(1 for l in log if l["kept"])
    json.dump({"config":cfg,"gate":"v2","changed":ch,"kept":kept,"reverted":ch-kept,
               "blocklist_flags_final":blocklist_density(P.join_blocks(final)),"log":log},
              open(f"{name}-report.json","w"),ensure_ascii=False,indent=1)
    print(f"  [{name}] v2 gate: changed={ch} kept={kept} reverted={ch-kept}")

for n in sys.argv[1:]: regate(n)
