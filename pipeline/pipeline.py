#!/usr/bin/env python3
"""Pilot: draft -> monolingual rewrite -> gated accept/revert, per config.

Design decisions, each tied to a finding from this project:
- Numbered-block translation (AST-lite): EN split into blocks, model returns the
  same numbered blocks. Guarantees EN<->MS<->rewrite alignment by construction,
  which is what makes segment-level gating possible at all.
- Rewrite pass sees ONLY Malay (no English) — the calques come from the English
  anchor. Prompt written in Malay (upstream repo found target-language prompts help).
- Default-revert: a changed segment must PASS every gate to be kept. The rewrite
  can lose an improvement but cannot land a regression a gate can see.
"""
import json, os, re, sys, time, urllib.request

CFG = {
 "budget":  {"draft":"qwen/qwen3.5-397b-a17b",   "rewrite":"google/gemma-4-26b-a4b-it", "gate":"google/gemma-4-26b-a4b-it"},
 "premium": {"draft":"anthropic/claude-sonnet-5","rewrite":"google/gemma-4-26b-a4b-it", "gate":"google/gemini-2.5-flash"},
}
KEY=os.environ["OPENROUTER_API_KEY"]
_HERE=os.path.dirname(os.path.abspath(__file__))
RULES=os.environ.get("MS_RULES_DIR", os.path.join(_HERE,"..","rules"))
TERMS=json.load(open(os.path.join(RULES,"ms-terms.json")))
BLOCK=json.load(open(os.path.join(RULES,"ms-indonesian-blocklist.json")))
TESTSET=json.load(open(os.path.join(_HERE,"..","eval","arbiter","testset.json")))

def call(model,text,temp=0.0,tries=4):
    # Qwen3.5 bills reasoning as completion tokens: 21,277 of 21,671 output tokens on a
    # single draft batch were reasoning, a 56x cost multiplier. Explicitly off everywhere;
    # it is a no-op for models that do not reason.
    body=json.dumps({"model":model,"messages":[{"role":"user","content":text}],
                     "temperature":temp,"reasoning":{"enabled":False}}).encode()
    last=""
    for a in range(tries):
        try:
            r=urllib.request.Request("https://openrouter.ai/api/v1/chat/completions",data=body,
              headers={"Authorization":f"Bearer {KEY}","Content-Type":"application/json"})
            with urllib.request.urlopen(r,timeout=300) as f: d=json.load(f)
            c=d["choices"][0]["message"]["content"]
            if c and c.strip(): return c
            last="empty response"
        except Exception as e: last=str(e)[:120]
        time.sleep(4*(a+1))
    raise RuntimeError(f"{model}: {last}")

# ---- block model ----
def split_blocks(md):
    """Split on blank lines; fences and HTML comments become single PROTECTED blocks."""
    out=[]; buf=[]; infence=False
    for line in md.splitlines():
        if line.startswith("```"):
            if buf and not infence: out.append(("text","\n".join(buf))); buf=[]
            buf.append(line)
            if infence: out.append(("prot","\n".join(buf))); buf=[]
            infence=not infence
            continue
        if infence: buf.append(line); continue
        if line.strip()=="":
            if buf: out.append(("text","\n".join(buf))); buf=[]
        else: buf.append(line)
    if buf: out.append(("prot" if infence else "text","\n".join(buf)))
    # A block that is ONLY html comments is protected. A block whose FIRST LINE is a
    # comment but which continues into prose is NOT — that pattern (INDEX/UPDATE marker
    # directly above the paragraph it annotates) covers 12 blocks / 799 words of this
    # chapter, and protecting the whole block silently ships them untranslated.
    out=[("prot",b) if k=="text" and all(CMT.match(l) or not l.strip()
                                         for l in b.split("\n")) else (k,b) for k,b in out]
    return out

CMT=re.compile(r"^\s*<!--.*?-->\s*$")

def strip_comments(block):
    """Pull full-line html comments out; the model never sees them, so it cannot mangle them."""
    lines=block.split("\n")
    keep={i:l for i,l in enumerate(lines) if CMT.match(l)}
    prose="\n".join(l for i,l in enumerate(lines) if i not in keep)
    return prose, keep

def restore_comments(prose, keep):
    if not keep: return prose
    plines=prose.split("\n"); out=[]; pi=0
    for i in range(len(keep)+len(plines)):
        if i in keep: out.append(keep[i])
        elif pi<len(plines): out.append(plines[pi]); pi+=1
    out.extend(plines[pi:])
    return "\n".join(out)

def join_blocks(blocks): return "\n\n".join(b for _,b in blocks)+"\n"

def numbered(chunks): return "\n\n".join(f"[[{i+1}]]\n{c}" for i,c in enumerate(chunks))
def parse_numbered(raw,n):
    parts=re.split(r"\[\[(\d+)\]\]",raw)
    got={}
    for i in range(1,len(parts)-1,2):
        try: got[int(parts[i])]=parts[i+1].strip()
        except Exception: pass
    return [got.get(i+1) for i in range(n)]

# ---- draft ----
def draft_prompt():
    trm="\n".join(f'- "{t["en"]}" -> {t["canonical"]}  (never: {", ".join(t.get("variants",[]))})' for t in TERMS["terms"])
    # ARM controls how many reviewer corrections the drafter is shown, so
    # "fixes what it was told" and "generalises to what it wasn't" stay separable.
    n=int(os.environ.get("EXEMPLARS","12"))
    real=[i for i in TESTSET if i["truth"]=="REAL"]
    ex=real[:n] if n else []
    def _strip(l): return re.sub(r"^\[[a-z]+\] ", "", l)
    exs="\n".join("- "+_strip(e["label"]) for e in ex) if ex else "(none supplied)"
    return f"""You are a native Malaysian author writing the Bahasa Melayu (Malaysia) edition of a professional book about AI for TVET instructors — not a literal translator. Standard Bahasa Melayu Malaysia, formal but natural; never Indonesian forms.

RULES
- Address the reader as "anda"; instructional we = "kita".
- No comma before "dan" in a series. Use "ialah" before nouns, never "Ini adalah".
- "tool" = "alat" (never "peranti"). "new" = "baharu". Spell numbers 0-9 as words except versions/steps/measurements.
- Keep in English: AI, TVET, PRISM, TRUST, BENCH, HANDS, GUARD, product names (ChatGPT, Claude, Copilot, Intel), institution names, and acronyms (RTO, ITE, BIBB, ILO, UNESCO). Framework letters keep their English word with a Malay gloss in parentheses on first use.
- TERMS (binding): 
{trm}

EDITOR PRECEDENT — a Malaysian reviewer corrected an earlier translation of this same book; write in the register these corrections point to:
{exs}

TASK: translate each numbered block below into Malay. Return the SAME numbered blocks [[n]] in the same order, nothing else. Preserve markdown (#, **, lists) exactly. Translate heading text. Do not add or drop sentences; every fact, number and name must survive exactly."""

def do_draft(model,blocks):
    src=[b for k,b in blocks if k=="text"]
    stripped=[strip_comments(b) for b in src]
    texts=[pr for pr,_ in stripped]
    out=[]
    B=8
    for i in range(0,len(texts),B):
        chunk=texts[i:i+B]
        raw=call(model, draft_prompt()+"\n\n"+numbered(chunk), temp=0.3)
        got=parse_numbered(raw,len(chunk))
        for j,g in enumerate(got):
            for _ in range(3):
                if g is not None and g.strip(): break
                g=(parse_numbered(call(model, draft_prompt()+"\n\n"+numbered([chunk[j]]), temp=0.3),1) or [None])[0]
            if not (g and g.strip()):
                # Falling back to the English source here is how 799 words shipped
                # untranslated in the first run. Fail loudly instead.
                raise RuntimeError(f"draft failed for block {i+j} after 3 retries; refusing to emit English")
            out.append(g)
        print(f"    draft {min(i+B,len(texts))}/{len(texts)}",flush=True)
    out=[restore_comments(t,keep) for t,(_,keep) in zip(out,stripped)]
    res=[]; it=iter(out)
    for k,b in blocks: res.append((k,b) if k=="prot" else ("text",next(it)))
    return res

# ---- rewrite (monolingual, Malay prompt) ----
RW=("Anda ialah editor buku profesional dari Malaysia. Baiki setiap perenggan bernombor di bawah supaya berbunyi "
    "seperti tulisan asal penulis Malaysia — Bahasa Melayu Malaysia yang baku, formal tetapi lancar. "
    "Betulkan terjemahan harfiah dan susunan ayat yang berbau Inggeris. JANGAN ubah maksud, fakta, angka, nama, "
    "istilah Inggeris yang dikekalkan (AI, TVET, PRISM, TRUST, BENCH, HANDS, GUARD, nama produk dan institusi), "
    "atau struktur markdown (#, **, senarai). Jika sesuatu perenggan sudah baik, kembalikannya tanpa perubahan. "
    "Kembalikan blok bernombor [[n]] yang sama sahaja.")
def do_rewrite(model,blocks):
    texts=[(i,b) for i,(k,b) in enumerate(blocks) if k=="text"]
    meta={i:strip_comments(b) for i,b in texts}
    out=dict()
    B=8
    for s in range(0,len(texts),B):
        chunk=texts[s:s+B]
        raw=call(model, RW+"\n\n"+numbered([meta[i][0] for i,_ in chunk]), temp=0.0)
        got=parse_numbered(raw,len(chunk))
        for (idx,orig),g in zip(chunk,got):
            # a failed rewrite falls back to the DRAFT (already Malay), never to English
            out[idx]=restore_comments(g,meta[idx][1]) if (g and g.strip()) else orig
        print(f"    rewrite {min(s+B,len(texts))}/{len(texts)}",flush=True)
    return [(k, out.get(i,b) if k=="text" else b) for i,(k,b) in enumerate(blocks)]

# ---- gates ----
NUM=re.compile(r"\d[\d.,%]*")
def nums(s):
    # strip trailing sentence punctuation: "2025," and "2025" are the same number.
    return sorted(re.sub(r"[.,]+$","",m) for m in NUM.findall(s))
DNT=["AI","TVET","PRISM","TRUST","BENCH","HANDS","GUARD","ChatGPT","Claude","Copilot","Intel","UNESCO","ILO","ITE","BIBB","RTO"]
FLAGSET={e["avoid_id"].lower() for e in BLOCK["flag"]}
VARIANTS=[(v.lower(),t["canonical"]) for t in TERMS["terms"] for v in t.get("variants",[])]
def words(s): return set(re.findall(r"[a-zA-ZÀ-ɏ'-]+",s.lower()))

def det_gate(draft_b, rw_b):
    reasons=[]
    if nums(draft_b)!=nums(rw_b): reasons.append("numbers changed")
    for d in DNT:
        if len(re.findall(rf"\b{re.escape(d)}\b",draft_b))>len(re.findall(rf"\b{re.escape(d)}\b",rw_b)):
            reasons.append(f"DNT lost: {d}")
    neww=words(rw_b)-words(draft_b)
    intro=[w for w in neww if w in FLAGSET]
    if intro: reasons.append(f"introduced flagged Indonesian form: {intro[:3]}")
    for v,canon in VARIANTS:
        if v in " ".join(neww) or (len(v.split())>1 and v in rw_b.lower() and v not in draft_b.lower()):
            reasons.append(f"introduced term variant '{v}' (canonical: {canon})")
    return reasons

def meaning_gate(model,en_b,rw_b):
    p=(f'ENGLISH ORIGINAL:\n{en_b}\n\nMALAY REVISION:\n{rw_b}\n\n'
       'Does the Malay preserve the FULL meaning of the English — every fact, quantity, negation, '
       'and who-does-what relationship? Style differences are fine. '
       'Return STRICT JSON: {"verdict":"PASS"|"FAIL","reason":"<short>"}')
    raw=call(model,p,temp=0.0)
    m=re.search(r'\{.*\}',raw,re.S)
    try:
        d=json.loads(m.group(0)); return d.get("verdict","FAIL").upper()=="PASS", d.get("reason","")
    except Exception: return False,"unparseable gate response"

def run(name):
    key=name.split("@")[0]; cfg=CFG[key]; t0=time.time()
    en=open("en-ch01.md").read()
    blocks=split_blocks(en)
    print(f"  [{name}] {len(blocks)} blocks ({sum(1 for k,_ in blocks if k=='text')} translatable)")
    draft=do_draft(cfg["draft"],blocks)
    open(f"{name}-draft.md","w").write(join_blocks(draft))
    rw=do_rewrite(cfg["rewrite"],draft)
    log=[]; final=[]
    for i,((k,en_b),(_,d_b),(_,r_b)) in enumerate(zip(blocks,draft,rw)):
        if k=="prot" or r_b.strip()==d_b.strip():
            final.append((k,d_b)); continue
        det=det_gate(d_b,r_b)
        if det:
            final.append((k,d_b)); log.append({"block":i,"kept":False,"why":"det:"+";".join(det)}); continue
        ok,reason=meaning_gate(cfg["gate"],en_b,r_b)
        final.append((k, r_b if ok else d_b))
        log.append({"block":i,"kept":ok,"why":reason})
    open(f"{name}-final.md","w").write(join_blocks(final))
    changed=len(log); kept=sum(1 for l in log if l["kept"])
    json.dump({"config":cfg,"changed":changed,"kept":kept,"reverted":changed-kept,"log":log},
              open(f"{name}-report.json","w"),ensure_ascii=False,indent=1)
    print(f"  [{name}] rewrite changed {changed} blocks -> kept {kept}, reverted {changed-kept}  ({time.time()-t0:.0f}s)")

if __name__ == "__main__":
    for name in sys.argv[1:]: run(name)
