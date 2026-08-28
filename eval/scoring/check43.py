#!/usr/bin/env python3
"""Score an output against ALL 43 reviewer flags, not just the mechanically-obvious 13.

Three-way, because "neither string present" is genuinely ambiguous and pretending
otherwise is how you get a flattering number:
  UNFIXED  - the flagged original is still there
  FIXED    - the reviewer's suggested wording is there and the original is gone
  CHANGED  - neither; the model wrote something else. Needs a human, not a guess.
"""
import json,re,sys,os
items=json.load(open("../arbiter-eval/testset.json"))
PAT=re.compile(r'^\[[a-z-]+\] "(.+?)" — reviewer suggests "(.+?)"\.')
FLAGS=[]
for it in items:
    if it["truth"]!="REAL": continue
    m=PAT.match(it["label"])
    if m: FLAGS.append((it["id"],it["cat"],m.group(1),m.group(2)))
def body(p):
    t=open(p,encoding="utf8").read(); t=re.sub(r"<!--.*?-->","",t,flags=re.S)
    return re.sub(r"\s+"," ",t)
def has(t,s):
    # Boundaries on EVERY pattern, phrases included: 31/43 flags are multi-word,
    # and substring matching scored "rasa tidak yakin" as present inside the
    # correct "berasa tidak yakin", marking good text UNFIXED (review finding).
    return re.search(rf"(?<![\w-]){re.escape(s)}(?![\w-])",t,re.I) is not None
def classify(t,orig,sug):
    o=has(t,orig)
    s=has(t,sug) if not sug.startswith("(") else False
    if o and not s: return "UNFIXED"
    if s and not o: return "FIXED"
    if o and s: return "UNFIXED"          # original still present somewhere
    return "CHANGED"
FILES=[a for a in sys.argv[1:] if os.path.exists(a)]
res={}
for f in FILES:
    t=body(f); res[f]=[(i,c,o,s,classify(t,o,s)) for i,c,o,s in FLAGS]
name=lambda f: os.path.basename(f).replace("-final.md","").replace(".md","")[:18]
print(f"{'flag':<5}{'cat':<10}{'flagged original':<42}" + "".join(f"{name(f):>20}" for f in FILES))
for k in range(len(FLAGS)):
    i,c,o,s=FLAGS[k][:4]
    print(f"{i:<5}{c:<10}{o[:40]:<42}" + "".join(f"{res[f][k][4]:>20}" for f in FILES))
print()
for f in FILES:
    v=[r[4] for r in res[f]]
    n=len(v); fx=v.count("FIXED"); un=v.count("UNFIXED"); ch=v.count("CHANGED")
    print(f"  {name(f):<20} FIXED={fx:<3} CHANGED={ch:<3} UNFIXED={un:<3}  (not-broken = {fx+ch}/{n} = {(fx+ch)/n:.0%})")
json.dump({name(f):[{"id":r[0],"cat":r[1],"orig":r[2],"sug":r[3],"verdict":r[4]} for r in res[f]] for f in FILES},
          open("check43-results.json","w"),ensure_ascii=False,indent=1)
