#!/usr/bin/env python3
"""Closed-world lexicon check (flag tier): every prose word must be accounted for.

Accounting sources, in order of cost:
  1. approved corpora (corpus-registry approved-malay) — human-reviewed words
  2. the PRPM ledger's VALID_MALAY entries — dictionary-verified words
  3. DNT tokens/phrases, the English source vocabulary, numbers
  4. morphology: an affixed form of an accounted root is accounted
Anything left is OOV — probably invented, misspelled, or Indonesian. This is
ADVISORY: it reports, it never blocks. Words the oracle later validates move
into the ledger and stop appearing.

Usage: check_lexicon.py <translated.md> [--en <source.md>]
"""
import json,re,sys,os,glob,pathlib,argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import msml
HERE=pathlib.Path(__file__).resolve().parent
PRE=["memper","menge","meng","meny","mem","men","me","ber","ter","di","pe","pen","pem","peng","peny","per","ke","se"]
SUF=["kannya","kanlah","lah","kah","nya","kan","an","i"]
def roots(w):
    out={w}
    for p in PRE:
        if w.startswith(p) and len(w)-len(p)>=3:
            r=w[len(p):]; out.add(r)
            for pf,c in (("mem","p"),("pem","p"),("men","t"),("pen","t"),("meng","k"),("peng","k"),("meny","s"),("peny","s")):
                if p==pf: out.add(c+r)
    for r in list(out):
        for s in SUF:
            if r.endswith(s) and len(r)-len(s)>=3: out.add(r[:-len(s)])
    if "-" in w: out|=set(w.split("-",1))
    return out
def vocab(text):
    masked = re.sub(r"\(\*[^)]{0,80}?\*\)", " ", msml.mask_body(text))  # retained-EN glosses
    masked = re.sub(r"[*_]", "", masked)  # letter-bolded mnemonics: **N**avigate must tokenize as Navigate, not N + avigate
    return {w.lower() for w in re.findall(r"[a-zA-ZÀ-ÖØ-öø-ÿ]+(?:-[a-zA-ZÀ-ÖØ-öø-ÿ]+)?", masked)}
def lexicon(extra=None):
    known=set(extra or ())
    reg=json.load(open(HERE/"corpus-registry.json"))
    for c in reg["corpora"]:
        if c["role"]!="approved-malay": continue
        p=pathlib.Path(os.path.expanduser(c["path"]))
        if not p.is_absolute(): p=(HERE/p).resolve()
        files=glob.glob(str(p/"*.md"))
        if not files and c.get("required",True):
            print(f"WARNING: approved-malay corpus missing at {c['path']} — lexicon runs weaker (advisory tool, continuing)")
        for f in files: known|=vocab(open(f,encoding="utf8").read())
    led=json.load(open(HERE/"ms-prpm-ledger.json"))
    known|={w for w,v in led.items() if v.get("verdict")=="VALID_MALAY"}
    dnt=json.load(open(HERE/"ms-dnt.json"))
    known|={t.lower() for t in dnt["tokens"]}
    for ph in dnt["phrases"]:                       # phrases tokenized: the lexicon is single-token
        known|={w.lower() for w in ph.split()}
    try:
        known|=set(json.load(open(HERE/"ms-wiktionary-lemmas.json")))
    except FileNotFoundError:
        pass
    # ORDER MATTERS: the authority subtraction comes LAST, after every union.
    # Wiktionary lists Indonesian-flavored entries as Malay ('solusi' is a lemma
    # there) — a union placed after this subtraction would silently re-vouch a
    # dictionary-rejected word. The ledger outranks every other tier.
    invalid={w for w,v in led.items() if v.get("verdict") in ("NO_ENTRY","VERIFIED_INDONESIAN")}
    known-=invalid
    return known, invalid
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("files",nargs="+",help="translated .md files (one lexicon build for all)")
    ap.add_argument("--en",action="append",default=[],help="EN source(s); vocab joins the accounting")
    a=ap.parse_args()
    en_extra=set()
    for ef in a.en: en_extra|=vocab(open(ef,encoding="utf8").read())
    known,invalid=lexicon(extra=en_extra)   # EN vocab unions INSIDE, before the authority subtraction
    for path in a.files:
        text=open(path,encoding="utf8").read()
        masked=msml.mask_body(text)
        # a ledger-rejected exact form is flagged even when morphology could vouch a
        # root ('jawaban' must not escape via 'jawab' + '-an'); the dictionary's no wins
        oov=sorted(w for w in vocab(text) if len(w)>2 and
                   (w in invalid or not (roots(w)&known)))
        print(f"  {os.path.basename(path)}: {len(oov)} unaccounted word form(s)")
        for w in oov:
            m=re.search(msml.word_pat(w), masked, re.I)
            ctx=re.sub(r"\s+"," ",masked[max(0,m.start()-40):m.start()+len(w)+30]) if m else ""
            print(f"    {w:<24} …{ctx}…")
if __name__=="__main__":
    main()
