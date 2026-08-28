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
        for f in glob.glob(str(p/"*.md")): known|=vocab(open(f,encoding="utf8").read())
    led=json.load(open(HERE/"ms-prpm-ledger.json"))
    known|={w for w,v in led.items() if v["verdict"]=="VALID_MALAY"}
    dnt=json.load(open(HERE/"ms-dnt.json"))
    known|={t.lower() for t in dnt["tokens"]}
    for ph in dnt["phrases"]:                       # phrases tokenized: the lexicon is single-token
        known|={w.lower() for w in ph.split()}
    try:
        known|=set(json.load(open(HERE/"ms-wiktionary-lemmas.json")))
    except FileNotFoundError:
        pass
    global _LEDGER_INVALID
    # ORDER MATTERS: the authority subtraction comes LAST, after every union.
    # Wiktionary lists Indonesian-flavored entries as Malay ('solusi' is a lemma
    # there) — a union placed after this subtraction would silently re-vouch a
    # dictionary-rejected word. The ledger outranks every other tier.
    _LEDGER_INVALID={w for w,v in led.items() if v.get("verdict") in ("NO_ENTRY","VERIFIED_INDONESIAN")}
    known-=_LEDGER_INVALID
    return known

_LEDGER_INVALID=set()
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("file"); ap.add_argument("--en")
    a=ap.parse_args()
    en_extra=vocab(open(a.en,encoding="utf8").read()) if a.en else set()
    known=lexicon(extra=en_extra)   # EN vocab unions INSIDE, before the authority subtraction
    text=open(a.file,encoding="utf8").read()
    # a ledger-rejected exact form is flagged even when morphology could vouch a
    # root ('jawaban' must not escape via 'jawab' + '-an'); the dictionary's no wins
    oov=sorted(w for w in vocab(text) if len(w)>2 and
               (w in _LEDGER_INVALID or not (roots(w)&known)))
    print(f"  {os.path.basename(a.file)}: {len(oov)} unaccounted word form(s)")
    for w in oov:
        m=re.search(rf"(?<![\w-]){re.escape(w)}(?![\w-])", msml.mask_body(text), re.I)
        ctx=re.sub(r"\s+"," ",msml.mask_body(text)[max(0,m.start()-40):m.start()+len(w)+30]) if m else ""
        print(f"    {w:<24} …{ctx}…")
if __name__=="__main__":
    main()
