#!/usr/bin/env python3
"""Classify reviewer corrections into SAFE-TO-ENFORCE vs NEEDS-RULING.

The gate is frequency in the text the reviewer actually read:
  occurs once  -> their flag covers every instance -> safe to enforce globally
  occurs many  -> they corrected ONE site, not the word -> enforcing it globally
                  would corrupt the valid uses (the tahu->tauhu failure mode)
This is deliberately conservative: a wrong promotion is permanent and silent,
a missed promotion just stays in the prompt layer where it works ~50% of the time.
"""
import json,re,sys
BASE="/Users/jphq/Downloads/AI+ TVET Malay (T2-133)/t2-133-ms-tvet-instructors-ms-chapters-v1.0.0/100-ch01-ai-dalam-pendidikan-vokasional-sekarang.md"
base=re.sub(r"<!--.*?-->","",open(BASE,encoding="utf8").read(),flags=re.S)
base=re.sub(r"```.*?```","",base,flags=re.S)
items=json.load(open("../arbiter-eval/testset.json"))
PAT=re.compile(r'^\[[a-z-]+\] "(.+?)" — reviewer suggests "(.+?)"\.')
def count(s):
    p=re.escape(s)
    if re.fullmatch(r"[\w'-]+",s): p=rf"(?<![\w-]){p}(?![\w-])"
    return len(re.findall(p,base,re.I))
safe,ruling,notarget=[],[],[]
for it in items:
    if it["truth"]!="REAL": continue
    m=PAT.match(it["label"])
    if not m: continue
    orig,sug=m.group(1),m.group(2)
    rec={"id":it["id"],"cat":it["cat"],"orig":orig,"sug":sug,"occurrences":count(orig)}
    if sug.startswith("("): notarget.append(rec)
    elif rec["occurrences"]==1: safe.append(rec)
    else: ruling.append(rec)
print(f"SAFE TO ENFORCE ({len(safe)}) — flagged word appears exactly once, so the correction is total:")
for r in safe: print(f"   {r['id']:>2} '{r['orig'][:40]}' -> '{r['sug'][:36]}'")
print(f"\nNEEDS HUMAN RULING ({len(ruling)}) — appears {'/'.join(str(r['occurrences']) for r in ruling[:8])}x; enforcing globally would break valid uses:")
for r in ruling: print(f"   {r['id']:>2} x{r['occurrences']:<3} '{r['orig'][:36]}' -> '{r['sug'][:32]}'")
print(f"\nNO MECHANICAL TARGET ({len(notarget)}) — human only:")
for r in notarget: print(f"   {r['id']:>2} '{r['orig'][:40]}' -> {r['sug'][:40]}")
json.dump({"safe_to_enforce":safe,"needs_ruling":ruling,"no_target":notarget},
          open("promotion-proposal.json","w"),ensure_ascii=False,indent=1)
print(f"\nwritten promotion-proposal.json")
