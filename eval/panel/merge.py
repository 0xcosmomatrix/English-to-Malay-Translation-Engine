#!/usr/bin/env python3
"""Merge panel verdicts, calibrate on the answer key, extract the contested set.

Calibration is the gate: a model whose accuracy on the 27 verified items falls
below 80% has its votes discarded entirely. The knowns were interleaved among
1,117 items, unlabeled, so a model could not treat them specially.
Vote: HARD only if every counted model says HARD (unanimity to enforce);
anything with a CONTEXT vote is context-tainted; NO_ANSWER never counts.
"""
import json,collections,pathlib
HERE=pathlib.Path(__file__).resolve().parent
ITEMS=json.load(open(HERE/"items.json"))
KEY=json.load(open(HERE/"answer_key.json"))
# The panel that actually answered: OpenRouter balance died mid-sweep, Google
# credits are depleted; GPT-5-mini runs direct on OpenAI and GLM on the free tier.
MODELS=["google_gemma-4-26b-a4b-it","openai-gpt5mini","glm-51-paid"]

votes={}   # id -> {model: verdict}
for m in MODELS:
    d={}
    for f in sorted((HERE/"results"/m).glob("chunk*.json")):
        d.update(json.load(open(f)))
    for i,v in d.items(): votes.setdefault(int(i),{})[m]=v

print(f"coverage: {len(votes)}/{len(ITEMS)} items have at least one verdict\n")
print("=== CALIBRATION on 27 verified items (never shown as such) ===")
counted=[]
for m in MODELS:
    ok=bad=miss=0; wrong=[]
    for it in ITEMS:
        k=KEY.get(it["avoid"].lower())
        if not k: continue
        v=votes.get(it["id"],{}).get(m)
        if not v or v["class"]=="NO_ANSWER": miss+=1; continue
        # SOFT counts as agreeing with CONTEXT (both mean: do not enforce)
        got="HARD_ERROR" if v["class"]=="HARD_ERROR" else "CONTEXT"
        if got==k: ok+=1
        else: bad+=1; wrong.append(f'{it["avoid"]}: said {v["class"]}, truth {k}')
    n=ok+bad; acc=ok/n if n else 0
    tag="COUNTED" if acc>=0.80 else "DISCARDED"
    counted.append(m) if acc>=0.80 else None
    print(f"  {m:<28} {ok}/{n} = {acc:.0%}  missing={miss}  -> {tag}")
    for w in wrong[:6]: print(f"      x {w}")
print()
if not counted: raise SystemExit("no model passed calibration — do not harden with this panel")

def cls(i):
    vs=[votes.get(i,{}).get(m) for m in counted]
    vs=[v for v in vs if v and v["class"]!="NO_ANSWER"]
    if not vs: return "UNANSWERED",[]
    cs=[v["class"] for v in vs]
    senses=[v["sense"] for v in vs if v.get("sense")]
    if all(c=="HARD_ERROR" for c in cs): return "HARD_UNANIMOUS",senses
    if any(c=="HARD_ERROR" for c in cs): return "DISPUTED",senses
    if any(c=="CONTEXT" for c in cs): return "CONTEXT",senses
    return "SOFT",senses

buckets=collections.Counter(); out={"HARD_UNANIMOUS":[],"DISPUTED":[],"CONTEXT":[],"SOFT":[],"UNANSWERED":[]}
for it in ITEMS:
    c,senses=cls(it["id"]); buckets[c]+=1
    out[c].append({**it,"senses":senses[:2]})
print("=== PANEL VERDICTS (counted models, unanimity required for HARD) ===")
for k in ("HARD_UNANIMOUS","DISPUTED","CONTEXT","SOFT","UNANSWERED"):
    print(f"  {k:<16} {buckets[k]}")
tb=[i for i in out["HARD_UNANIMOUS"]+out["DISPUTED"] if i["kind"]=="termbase"]
hs=[i for i in out["CONTEXT"]+out["SOFT"] if i["kind"]=="hardset"]
print(f"\n  termbase variants judged HARD/DISPUTED (fine): {len(tb)}")
print(f"  MY hardset entries judged CONTEXT/SOFT (my bugs): {len(hs)}")
for i in hs: print(f"      {i['avoid']}: {(i['senses'] or ['-'])[0][:80]}")
json.dump(out,open("panel-verdicts.json","w"),ensure_ascii=False,indent=1)
print("\nwritten panel-verdicts.json")
print("PRPM verification targets: DISPUTED + all termbase items + all hardset items + "
      "a HARD_UNANIMOUS spot-check sample")
