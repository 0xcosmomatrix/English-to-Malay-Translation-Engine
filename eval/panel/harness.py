#!/usr/bin/env python3
"""Panel audit of the ms-MY watchlist/termbase. One chunk = ~190 items x 3 models.

Usage: harness.py --chunk N     (results/<model-slug>/chunkN.json; skip if exists)

Classes the auditors assign to each AVOID form:
  HARD_ERROR - not Standard Malaysian Malay in any common sense (Indonesian-only
               form or plain misspelling). Safe to flag as an error anywhere.
  CONTEXT    - a real Standard Malay word/phrase in some sense or register, even
               if the paired sense prefers the other form. Auto-enforcement would
               corrupt correct text. Auditor must name the valid sense.
  SOFT       - both forms acceptable in Malaysian usage; style preference only.
The answer key stays on disk here and is never sent to any model.
"""
import json,os,re,sys,time,urllib.request,argparse,pathlib
HERE=pathlib.Path(__file__).resolve().parent
for line in open(os.path.expanduser("~/Downloads/env")):
    line=line.strip()
    if line and not line.startswith("#") and "=" in line:
        k,v=line.split("=",1); os.environ.setdefault(k.strip(),v.strip().strip('"'))
KEY=os.environ["OPENROUTER_API_KEY"]
MODELS=["google/gemma-4-26b-a4b-it","z-ai/glm-5.1","google/gemini-2.5-flash"]
ITEMS=json.load(open(HERE/"items.json"))
CHUNK=190; BATCH=38

HEAD="""You are a Malaysian Malay lexicography auditor reviewing a watchlist used to police
book translations for Indonesian interference. For EACH numbered entry below, judge the
AVOID form as a word/phrase of Standard Malaysian Malay (Bahasa Melayu Malaysia, DBP norm):

- HARD_ERROR: the avoid form is NOT Standard Malaysian Malay in any common sense — an
  Indonesian-only form or a misspelling. Flagging it as an error anywhere is safe.
- CONTEXT: the avoid form IS a legitimate Standard Malay word or phrase in some sense or
  register (even if a different sense prefers the paired form). Auto-replacing it would
  corrupt correct text. You MUST name the valid sense briefly.
- SOFT: both forms are acceptable Malaysian usage; the pairing is a style preference.

Judge the AVOID form itself, not the pairing's intent. Be conservative: when unsure
between HARD_ERROR and CONTEXT, choose CONTEXT.
Return STRICT JSON: {"verdicts":[{"n":1,"class":"...","sense":"..."}, ...]} with exactly
one verdict per entry, in order. "sense" is required for CONTEXT, else "".
"""

def call(model,text,tries=5):
    body=json.dumps({"model":model,"messages":[{"role":"user","content":text}],
                     "temperature":0,"reasoning":{"enabled":False}}).encode()
    last=""
    for a in range(tries):
        try:
            r=urllib.request.Request("https://openrouter.ai/api/v1/chat/completions",data=body,
              headers={"Authorization":f"Bearer {KEY}","Content-Type":"application/json"})
            with urllib.request.urlopen(r,timeout=240) as f: d=json.load(f)
            c=d["choices"][0]["message"]["content"]
            if c and c.strip(): return c
            last="empty"
        except Exception as e: last=str(e)[:120]
        time.sleep(4*(a+1))
    raise RuntimeError(f"{model}: {last}")

def parse(raw,n):
    m=re.search(r"\{.*\}",re.sub(r"^```(?:json)?|```$","",raw.strip(),flags=re.M),re.S)
    if not m: return None
    try: v=json.loads(m.group(0))["verdicts"]
    except Exception: return None
    out={}
    for x in v:
        try: out[int(x["n"])]={"class":str(x.get("class","")).upper(),"sense":str(x.get("sense",""))[:120]}
        except Exception: pass
    return [out.get(i+1) for i in range(n)]

def fmt(batch):
    L=[]
    for j,it in enumerate(batch):
        pair=f'AVOID "{it["avoid"]}"'
        if it["use"]: pair+=f' (list prefers "{it["use"]}"'+(f' for "{it["en"]}")' if it["en"] else ")")
        L.append(f"{j+1}. {pair}")
    return HEAD+"\n"+"\n".join(L)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--chunk",type=int,required=True); a=ap.parse_args()
    lo,hi=a.chunk*CHUNK,min((a.chunk+1)*CHUNK,len(ITEMS))
    chunk=ITEMS[lo:hi]
    if not chunk: print("empty chunk"); return
    fails=0
    for model in MODELS:
        slug=model.replace("/","_")
        outd=HERE/"results"/slug; outd.mkdir(parents=True,exist_ok=True)
        outf=outd/f"chunk{a.chunk}.json"
        if outf.exists(): print(f"  skip {slug} chunk{a.chunk} (exists)"); continue
        res={}
        for b in range(0,len(chunk),BATCH):
            batch=chunk[b:b+BATCH]
            got=None
            for attempt in range(3):
                try: got=parse(call(model,fmt(batch)),len(batch))
                except RuntimeError as e: print(f"  API fail {slug}: {e}"); got=None
                if got and all(g is not None for g in got): break
                got=None
            if got is None:
                fails+=len(batch)
                for it in batch: res[str(it["id"])]={"class":"NO_ANSWER","sense":""}
            else:
                for it,g in zip(batch,got): res[str(it["id"])]=g
            print(f"  {slug} chunk{a.chunk}: {min(b+BATCH,len(chunk))}/{len(chunk)}",flush=True)
        json.dump(res,open(outf,"w"),ensure_ascii=False)
    print(f"DONE chunk{a.chunk} items={len(chunk)} failures={fails}")

if __name__=="__main__":
    main()
