#!/usr/bin/env python3
"""Direct-API panelists (OpenRouter balance exhausted mid-sweep; Gemma survived).
Usage: harness2.py --provider gemini|openai   (runs all 6 chunks, skip-if-exists)"""
import json,os,re,sys,time,urllib.request,urllib.error,argparse,pathlib,importlib.util
HERE=pathlib.Path(__file__).resolve().parent
for line in open(os.path.expanduser("~/Downloads/env")):
    line=line.strip()
    if line and not line.startswith("#") and "=" in line:
        k,v=line.split("=",1); os.environ.setdefault(k.strip(),v.strip().strip('"'))
spec=importlib.util.spec_from_file_location("h1",HERE/"harness.py")
# import only the shared pieces (HEAD/fmt/parse/ITEMS) without running main()
src=(HERE/"harness.py").read_text().replace("\nmain()\n","\n")
h1=importlib.util.module_from_spec(spec); exec(compile(src,"harness.py","exec"),h1.__dict__)
ITEMS,fmt,parse=h1.ITEMS,h1.fmt,h1.parse
CHUNK,BATCH=h1.CHUNK,h1.BATCH

def call_gemini(text,tries=5):
    key=os.environ["GEMINI_API_KEY"]
    model=os.environ.get("GEM_MODEL","gemini-3.5-flash")
    url=f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    body=json.dumps({"contents":[{"parts":[{"text":text}]}],
        "generationConfig":{"temperature":0,"thinkingConfig":{"thinkingBudget":0}}}).encode()
    last=""
    for a in range(tries):
        try:
            r=urllib.request.Request(url,data=body,headers={"Content-Type":"application/json"})
            with urllib.request.urlopen(r,timeout=240) as f: d=json.load(f)
            parts=d["candidates"][0]["content"]["parts"]
            t="".join(p.get("text","") for p in parts)
            if t.strip(): return t
            last="empty"
        except urllib.error.HTTPError as e:
            last=f"HTTP {e.code}: {e.read().decode()[:100]}"
            if e.code==400 and "thinking" in last.lower():
                body=json.dumps({"contents":[{"parts":[{"text":text}]}],
                    "generationConfig":{"temperature":0}}).encode(); continue
        except Exception as e: last=str(e)[:120]
        time.sleep(5*(a+1))
    raise RuntimeError(f"gemini: {last}")

def call_openai(text,tries=5):
    key=os.environ["OPENAI_API_KEY"]; model=os.environ.get("OAI_MODEL","gpt-5-mini")
    body=json.dumps({"model":model,"messages":[{"role":"user","content":text}],
                     "reasoning_effort":"low"}).encode()
    last=""
    for a in range(tries):
        try:
            r=urllib.request.Request("https://api.openai.com/v1/chat/completions",data=body,
              headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"})
            with urllib.request.urlopen(r,timeout=300) as f: d=json.load(f)
            t=d["choices"][0]["message"]["content"]
            if t and t.strip(): return t
            last="empty"
        except urllib.error.HTTPError as e:
            msg=e.read().decode()[:200]; last=f"HTTP {e.code}: {msg}"
            if e.code in (400,404) and "reasoning_effort" in msg:
                body=json.dumps({"model":model,"messages":[{"role":"user","content":text}]}).encode(); continue
            if e.code==404:  # model name miss -> fall back
                model="gpt-5"; body=json.dumps({"model":model,"messages":[{"role":"user","content":text}]}).encode(); continue
        except Exception as e: last=str(e)[:120]
        time.sleep(5*(a+1))
    raise RuntimeError(f"openai: {last}")

def call_glm(text,tries=6):
    # OpenRouter free tier still serves when prepaid balance is gone; 429s are just rate limits.
    key=os.environ["OPENROUTER_API_KEY"]; model=os.environ.get("GLM_MODEL","z-ai/glm-5.2:free")
    body=json.dumps({"model":model,"messages":[{"role":"user","content":text}],
                     "temperature":0,"reasoning":{"enabled":False}}).encode()
    last=""
    for a in range(tries):
        try:
            r=urllib.request.Request("https://openrouter.ai/api/v1/chat/completions",data=body,
              headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"})
            with urllib.request.urlopen(r,timeout=300) as f: d=json.load(f)
            t=d["choices"][0]["message"]["content"]
            if t and t.strip(): return t
            last="empty"
        except urllib.error.HTTPError as e:
            last=f"HTTP {e.code}: {e.read().decode()[:100]}"
            if e.code==429: time.sleep(20*(a+1)); continue
        except Exception as e: last=str(e)[:120]
        time.sleep(5*(a+1))
    raise RuntimeError(f"glm: {last}")

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--provider",required=True,choices=["gemini","openai","glm"])
    a=ap.parse_args()
    call={"gemini":call_gemini,"openai":call_openai,"glm":call_glm}[a.provider]
    slug={"gemini":"gemini-3.5-flash-direct","openai":"openai-gpt5mini","glm":os.environ.get("GLM_SLUG","glm-free")}[a.provider]
    outd=HERE/"results"/slug; outd.mkdir(parents=True,exist_ok=True)
    fails=0
    for c in range(6):
        lo,hi=c*CHUNK,min((c+1)*CHUNK,len(ITEMS)); chunk=ITEMS[lo:hi]
        outf=outd/f"chunk{c}.json"
        if not chunk or outf.exists(): continue
        res={}
        for b in range(0,len(chunk),BATCH):
            batch=chunk[b:b+BATCH]; got=None
            for _ in range(3):
                try: got=parse(call(fmt(batch)),len(batch))
                except RuntimeError as e: print(f"  API fail {slug}: {e}",flush=True); got=None
                if got and all(g is not None for g in got): break
                got=None
            if got is None:
                fails+=len(batch)
                for it in batch: res[str(it["id"])]={"class":"NO_ANSWER","sense":""}
            else:
                for it,g in zip(batch,got): res[str(it["id"])]=g
            print(f"  {slug} chunk{c}: {min(b+BATCH,len(chunk))}/{len(chunk)}",flush=True)
        json.dump(res,open(outf,"w"),ensure_ascii=False)
    print(f"DONE {slug} failures={fails}")
main()
