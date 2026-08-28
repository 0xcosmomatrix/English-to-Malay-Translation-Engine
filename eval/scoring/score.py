#!/usr/bin/env python3
"""Score baseline (shipped, human-reviewed) vs budget-final vs premium-final."""
import json,re,sys,os
def body(p):
    t=open(p,encoding="utf8").read()
    t=re.sub(r"```.*?```","",t,flags=re.S); t=re.sub(r"<!--.*?-->","",t,flags=re.S)
    return t
def hits(t,pat,flags=re.I): return len(re.findall(pat,t,flags))
FILES={"baseline(shipped)":os.path.expanduser("~/Downloads/AI+ TVET Malay (T2-133)/t2-133-ms-tvet-instructors-ms-chapters-v1.0.0/100-ch01-ai-dalam-pendidikan-vokasional-sekarang.md"),
       "budget RSN-ON":"budget-rsnON-final.md","budget RSN-OFF":"budget-final.md","budget ARM43":"budget@arm43-final.md","premium":"premium-final.md"}
# Tier-1: reviewer flags checkable mechanically. pattern -> (label, want_zero)
CHECKS=[
 (r"(?<![\w-])baru(?![\w-])","'baru' (want baharu)",True),
 (r"pasca[ -]menengah","'pasca menengah' (want pascamenengah)",True),
 (r"penglihatan komputer","'penglihatan komputer' (want visi komputer)",True),
 (r"(?<![\w-])pelatih","'pelatih' (want perantis)",True),
 (r"bahasa semula ?jadi","'bahasa semula jadi' (reviewer: tabii)",True),
 (r"seni ?bina","'seni bina' (want kerangka dasar)",True),
 (r"menutup jurang","'menutup jurang' (want merapatkan)",True),
 (r"(?<![\w-])Di akhir","'Di akhir' (want Pada akhir)",True),
 (r"Registered Training Organisation","untranslated RTO name",True),
 (r"yang memimpin","'yang Memimpin' heading (want Peneraju)",True),
 (r"(?<![\w-])peranti(?![\w-])","'peranti' (want alat)",True),
 (r"melibatkan diri dengan","'melibatkan diri dengan' (weak diction)",True),
 (r"(?<![\w-])(aktivitas|jawaban|karena|akun|kebijakan|kualitas|fasilitas|universitas|komunitas|napas)(?![\w-])","hard Indonesian forms",True),
]
FILES={k:v for k,v in FILES.items() if os.path.exists(v)}
texts={k:body(v) for k,v in FILES.items()}
w=max(len(l) for _,l,_ in CHECKS)+2
print(f"{'reviewer-flag check':<{w}}"+ "".join(f"{k:>20}" for k in FILES))
score={k:0 for k in FILES}
for pat,label,_ in CHECKS:
    row=f"{label:<{w}}"
    for k,t in texts.items():
        n=hits(t,pat); row+=f"{n:>20}"; score[k]+= (n==0)
    print(row)
print(f"{'CLEAN (of '+str(len(CHECKS))+')':<{w}}"+ "".join(f"{score[k]:>20}" for k in FILES))
print(f"\n{'prompt vs prom (open question)':<{w}}"+ "".join(f"{str(hits(t,'(?<![a-z])prompt'))+'/'+str(hits(t,'(?<![a-z])prom(?![a-z])')):>20}" for t in texts.values()))
print(f"{'words':<{w}}"+ "".join(f"{len(t.split()):>20}" for t in texts.values()))
# English residue — the check whose absence let 799 untranslated words pass as "clean".
EN_W={"the","and","of","to","in","is","are","for","with","that","this","from","has","have",
"they","their","which","been","were","was","can","will","not","but","more","than","who","how"}
MS_W={"yang","dan","untuk","dengan","ini","itu","adalah","ialah","dalam","pada","tidak","akan",
"boleh","mereka","beliau","anda","kita","daripada","oleh","telah","juga","atau","kepada","sebagai"}
print("\n=== untranslated English residue ===")
for k,t2 in texts.items():
    bs=[b for b in t2.split("\n\n") if len(b.split())>=12]; bad=0; bw=0
    for b in bs:
        w=set(re.findall(r"[a-z']+",b.lower()))
        if len(w&EN_W)>=4 and len(w&EN_W)>len(w&MS_W): bad+=1; bw+=len(b.split())
    print(f"  {k:<20} english-dominant blocks={bad:<3} words={bw:<5} " + ("CLEAN" if not bad else "<-- FAIL"))

# fact alignment vs EN
en=body("en-ch01.md"); import collections
MW={"0":"sifar","1":"satu","2":"dua","3":"tiga","4":"empat","5":"lima","6":"enam","7":"tujuh","8":"lapan","9":"sembilan"}
def numset(t): return collections.Counter(re.sub(r"[.,]+$","",m) for m in re.findall(r"\d[\d.,%]*",t))
def unaccounted(target, missing):
    """Style-aware: 0-9 may be spelled out (MS rule); '%' may be the word 'peratus'."""
    out=[]
    for n in missing:
        if n in MW and re.search(rf"(?<![\w-]){MW[n]}(?![\w-])",target,re.I): continue
        if n.endswith("%") and re.search(rf"{re.escape(n[:-1])}\s*(%|peratus)",target,re.I): continue
        out.append(n)
    return out
ne=numset(en)
print(f"\n=== number alignment vs English ({sum(ne.values())} numbers in source; style-aware) ===")
for k,t in texts.items():
    nt=numset(t); miss=list((ne-nt).elements()); extra=list((nt-ne).elements())
    real=unaccounted(t,miss)
    print(f"  {k:<20} raw_missing={len(miss):<3} unaccounted={len(real):<3} extra={len(extra):<3} " +
          (f" -> {real[:8]}" if real else "-> all facts present"))
# rewrite reports
print("\n=== rewrite pass ===")
for k in ("budget","premium"):
    try:
        r=json.load(open(f"{k}-report.json"))
        print(f"  {k:<8} changed={r['changed']}  kept={r['kept']}  reverted={r['reverted']}")
        for l in r["log"]:
            if not l["kept"]: print(f"           revert b{l['block']}: {l['why'][:90]}")
    except FileNotFoundError: print(f"  {k}: no report yet")
