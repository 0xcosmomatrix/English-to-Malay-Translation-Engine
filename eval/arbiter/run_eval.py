import json,os,re,sys,time,urllib.request,urllib.error

MODELS=[("google/gemma-4-26b-a4b-it","Gemma 4 26B",75.54),
        ("google/gemma-4-31b-it","Gemma 4 31B",75.40),
        ("qwen/qwen3.5-397b-a17b","Qwen 3.5 397B",74.00),
        ("qwen/qwen3.5-122b-a10b","Qwen 3.5 122B",70.51),
        ("z-ai/glm-5.1","GLM 5.1",69.76),
        ("anthropic/claude-haiku-4.5","Haiku 4.5 (CONTROL)",55.25)]
KEY=os.environ["OPENROUTER_API_KEY"]; BATCH=20
items=json.load(open("testset.json"))

# The engine's real arbiter prompt, verbatim (src/lib/arbiter.ts).
def prompt(batch):
    head=(f'You are an independent editorial arbiter for a Malay book translation. A separate QC pass '
      f'flagged the items below. For EACH item, decide: REAL (a competent bilingual editor would actually '
      f'change the text — the issue is genuine and worth a fix) or NIT (technically defensible but not worth '
      f'acting on — a minor stylistic preference, hairsplitting, an equally-valid alternate rendering). Most '
      f'flagged items in a well-drafted chapter are NITs; a QC judge under pressure to report something tends '
      f'to over-flag, and your job is to catch that.\n'
      f'Judge each item independently, in the order given. Return STRICT JSON: {{"verdicts":["REAL"|"NIT", ...]}} '
      f'— exactly {len(batch)} verdicts, same order as the items below.\n\n')
    return head+"\n".join(f'{i+1}. {b["label"]}' for i,b in enumerate(batch))

def call(model,text,tries=4):
    body=json.dumps({"model":model,"messages":[{"role":"user","content":text}],"temperature":0}).encode()
    last=""
    for a in range(tries):
        try:
            r=urllib.request.Request("https://openrouter.ai/api/v1/chat/completions",data=body,
              headers={"Authorization":f"Bearer {KEY}","Content-Type":"application/json"})
            with urllib.request.urlopen(r,timeout=180) as f: d=json.load(f)
            return d["choices"][0]["message"]["content"] or ""
        except Exception as e:
            last=str(e)[:120]; time.sleep(3*(a+1))
    raise RuntimeError(last)

def verdicts(raw,n):
    s=re.sub(r"^```(?:json)?|```$","",raw.strip(),flags=re.M).strip()
    m=re.search(r'\{.*\}',s,re.S)
    if m:
        try:
            v=json.loads(m.group(0)).get("verdicts",[])
            if len(v)==n: return [str(x).upper() for x in v]
        except Exception: pass
    found=re.findall(r'\b(REAL|NIT)\b',s.upper())
    return found[:n] if len(found)>=n else found+["?"]*(n-len(found))

results={}
for mid,label,cult in MODELS:
    got=[]
    try:
        for i in range(0,len(items),BATCH):
            b=items[i:i+BATCH]
            got+=verdicts(call(mid,prompt(b)),len(b))
    except Exception as e:
        print(f"  {label:<22} FAILED: {e}"); continue
    tp=sum(1 for it,v in zip(items,got) if it["truth"]=="REAL" and v=="REAL")
    fn=sum(1 for it,v in zip(items,got) if it["truth"]=="REAL" and v!="REAL")
    tn=sum(1 for it,v in zip(items,got) if it["truth"]=="NIT"  and v=="NIT")
    fp=sum(1 for it,v in zip(items,got) if it["truth"]=="NIT"  and v!="NIT")
    rec=tp/(tp+fn) if tp+fn else 0; spec=tn/(tn+fp) if tn+fp else 0
    results[label]={"model":mid,"cultural":cult,"recall":rec,"specificity":spec,
                    "balanced":(rec+spec)/2,"tp":tp,"fn":fn,"tn":tn,"fp":fp,"verdicts":got}
    print(f"  {label:<22} recall={rec:5.1%}  specificity={spec:5.1%}  balanced={((rec+spec)/2):5.1%}")
json.dump({"items":items,"results":results},open("results.json","w"),ensure_ascii=False,indent=1)
print("\n  written results.json")
