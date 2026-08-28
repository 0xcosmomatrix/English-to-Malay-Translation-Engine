#!/usr/bin/env python3
"""Rulebook maintenance for ms-MY: propose new terms, audit enforced ones.

Design rule, learned the hard way: nothing is ever auto-promoted into `terms`.
New corrections land in `open_questions` with evidence attached, and a human
rules on them. The reason is in this file's own history — `baru -> baharu` was
promoted straight from a review, but `baru sahaja` ("only just") is correct
Malay, so the enforced rule would corrupt valid text. Frequency evidence makes
the ruling cheap; it does not make it unnecessary.

  rulebook.py audit   <corpus.md> [...]        # risk-check the ENFORCED terms
  rulebook.py propose <corrections.json> <corpus.md> [...]  # -> open_questions
  rulebook.py rule    <en> --accept|--reject   # apply a human ruling
"""
import json, sys, os as _os
sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import msml,re,sys,shutil,pathlib,argparse,datetime

HERE=pathlib.Path(__file__).resolve().parent
TERMS=HERE/"ms-terms.json"

def load(): return json.loads(TERMS.read_text(encoding="utf8"))
def save(d):
    shutil.copy(TERMS, TERMS.with_suffix(".json.bak"))
    TERMS.write_text(json.dumps(d,ensure_ascii=False,indent=1)+"\n",encoding="utf8")

def body(p):
    return msml.mask_body(pathlib.Path(p).read_text(encoding="utf8"))

word_pat = msml.word_pat

def occurrences(term,text): return [m for m in re.finditer(word_pat(term),text,re.I)]
def contexts(term,text,n=3):
    return [re.sub(r"\s+"," ",text[max(0,m.start()-42):m.start()+len(term)+30]).strip()
            for m in occurrences(term,text)[:n]]

def cmd_audit(args):
    d=load(); corpus="\n".join(body(p) for p in args.corpus)
    print("Auditing ENFORCED terms against the corpus.")
    print("A variant still present means the rule is either unapplied or unapplicable.\n")
    risky=0
    for t in d["terms"]:
        for v in t.get("variants",[]):
            occ=occurrences(v,corpus)
            if not occ: continue
            risky+=1
            print(f"  '{v}' -> '{t['canonical']}'  ({t['en']})  x{len(occ)}")
            for c in contexts(v,corpus): print(f"        …{c}…")
            print(f"        ^ if any of these reads correctly as-is, this rule is "
                  f"context-dependent and must NOT be enforced globally.\n")
    print(f"  {risky} enforced variant(s) still present." if risky else "  clean — no enforced variant present.")

def cmd_propose(args):
    d=load(); corpus="\n".join(body(p) for p in args.corpus)
    known={t["canonical"].lower() for t in d["terms"]}
    known|={v.lower() for t in d["terms"] for v in t.get("variants",[])}
    known|={q["en"].lower() for q in d["open_questions"]}
    raw=json.loads(pathlib.Path(args.corrections).read_text(encoding="utf8"))
    cands=raw if isinstance(raw,list) else raw.get("corrections",[])
    # AUTHORITY GUARD: a candidate that proposes moving AWAY from an enforced
    # canonical, or re-introducing a ruled-out variant, re-litigates a settled
    # ruling — auto-rejected with the ruling cited. Reopening is a deliberate
    # human act on the term entry, never a side effect of mining old verdicts.
    canon={t["canonical"].lower(): t for t in d["terms"]}
    ruled_out={v.lower(): t for t in d["terms"] for v in t.get("variants",[])}
    added=0
    for c in cands:
        orig,sug=c.get("orig",""),c.get("sug","")
        if not orig or not sug or sug.startswith("("): continue
        hit=canon.get(orig.lower()) or ruled_out.get(sug.lower())
        if hit:
            print(f"  REJECTED (standing ruling): {orig!r} -> {sug!r} conflicts with "
                  f"'{hit['canonical']}' [{hit.get('source','')}]")
            continue
        if orig.lower() in known or sug.lower() in known: continue
        n=len(occurrences(orig,corpus))
        risk=("context-free candidate: appears once, so the correction is total"
              if n==1 else
              f"CONTEXT-DEPENDENT: appears {n}x — reviewer corrected one site, not the word"
              if n>1 else
              "not present in corpus — nothing to enforce; record only")
        d["open_questions"].append({
            "en": c.get("en") or orig,
            "options": [f"{sug} (reviewer)", f"{orig} (current)"],
            "status": "UNRESOLVED",
            "evidence": {"occurrences": n, "samples": contexts(orig,corpus), "risk": risk},
            "why": c.get("why") or f"reviewer correction; {risk}",
            "source": args.source,
        })
        known.add(orig.lower()); added+=1
    if added: save(d)
    print(f"  {added} correction(s) added to open_questions (0 to terms — promotion needs a ruling).")
    print(f"  ruling sheet: {len([q for q in d['open_questions'] if q['status']=='UNRESOLVED'])} unresolved total.")

def cmd_rule(args):
    d=load(); q=next((x for x in d["open_questions"] if x["en"].lower()==args.en.lower()),None)
    if not q: sys.exit(f"no open question for {args.en!r}")
    if args.reject:
        q["status"]=f"REJECTED {datetime.date.today()}"; save(d)
        print(f"  '{args.en}' marked rejected; stays out of the enforced layer."); return
    canon=args.canonical or q["options"][0].split(" (")[0]
    variant=q["options"][1].split(" (")[0] if len(q["options"])>1 else None
    ev=q.get("evidence",{})
    # Oracle leg: a variant becomes enforced only with a PRPM ruling on file.
    # No ledger entry -> look the word up first; VALID_MALAY -> it may never be
    # word-level enforced (the kanker/kursi lesson: obscure real senses exist).
    if variant:
        led=json.loads((HERE/"ms-prpm-ledger.json").read_text(encoding="utf8")) if (HERE/"ms-prpm-ledger.json").exists() else {}
        o=led.get(variant.lower(),{}).get("verdict")
        if o is None and not args.force:
            sys.exit(f"  refusing: no PRPM ledger ruling for {variant!r}. Look it up "
                     f"(https://prpm.dbp.gov.my/Cari1?keyword={variant}), record it in "
                     f"ms-prpm-ledger.json, then re-run. --force skips only with a human ruling.")
        if o=="VALID_MALAY":
            sys.exit(f"  refusing: PRPM records {variant!r} as valid Malay "
                     f"({led[variant.lower()]['evidence'][:60]}) — word-level enforcement "
                     f"would corrupt correct text. This cannot be forced.")
    if ev.get("occurrences",0)>1 and not args.force:
        sys.exit(f"  refusing: '{args.en}' appears {ev['occurrences']}x and is marked "
                 f"context-dependent.\n  Enforcing it would rewrite valid uses. Re-run with "
                 f"--force only if a native reviewer confirms every occurrence should change.")
    d["terms"].append({"en":q["en"],"canonical":canon,
                       "variants":[variant] if variant else [],
                       "why":f"human ruling {datetime.date.today()}; {q.get('why','')}"[:200],
                       "source":q.get("source","ruling")})
    q["status"]=f"RESOLVED {datetime.date.today()} -> {canon}"
    save(d); print(f"  '{args.en}' -> enforced as '{canon}'.")

ap=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
sub=ap.add_subparsers(dest="cmd",required=True)
a=sub.add_parser("audit");   a.add_argument("corpus",nargs="+"); a.set_defaults(f=cmd_audit)
b=sub.add_parser("propose"); b.add_argument("corrections"); b.add_argument("corpus",nargs="+")
b.add_argument("--source",default=f"review-{datetime.date.today():%Y-%m}"); b.set_defaults(f=cmd_propose)
c=sub.add_parser("rule");    c.add_argument("en"); c.add_argument("--accept",action="store_true")
c.add_argument("--reject",action="store_true"); c.add_argument("--canonical"); c.add_argument("--force",action="store_true")
c.set_defaults(f=cmd_rule)
args=ap.parse_args(); args.f(args)
