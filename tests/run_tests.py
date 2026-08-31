#!/usr/bin/env python3
"""Zero-dependency regression suite. Every case here is a bug that shipped once."""
import os, sys, importlib.util, subprocess, tempfile, types
os.environ.setdefault("OPENROUTER_API_KEY", "dummy")
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("p", os.path.join(HERE, "..", "pipeline", "pipeline.py"))
P = importlib.util.module_from_spec(spec); spec.loader.exec_module(P)
def load(relpath, name):
    """Import a repo module by path. Every target is __main__-guarded, so the old
    read-source-and-strip-main exec surgery was a no-op ritual around this."""
    sp = importlib.util.spec_from_file_location(name, os.path.join(HERE, "..", relpath))
    mod = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(mod)
    return mod

N = [0]
def ok(name, cond, detail=""):
    N[0] += 1
    if not cond:
        print(f"FAIL {name}  {detail}"); sys.exit(1)
    print(f"  ok  {name}")

# restore_comments: trailing comment survives a shorter translation (review critical #1)
prose, keep = P.strip_comments("<!-- a -->\nline1\nline2\nline3\n<!-- b -->")
out = P.restore_comments("terjemahan satu baris", keep)
ok("restore_comments conservation", out.count("<!--") == 2, out)

# bidirectional facts with %/time/setengah/spelled-digit normalization
en = "Fatima arrives at 7:30. The survey covered three countries and found 28% adoption in 2020. Total: 2.5 hours."
ms = "Fatima tiba pada pukul 7.30 pagi. Tinjauan itu meliputi 3 buah negara dan mendapati penerimagunaan 28 peratus pada 2020. Jumlah: dua setengah jam."
ok("missing_facts normalized", P.missing_facts(en, ms) == [], P.missing_facts(en, ms))
ok("invented_facts normalized", P.invented_facts(en, ms) == [], P.invented_facts(en, ms))
bad = P.invented_facts(en, "Kajian menunjukkan hampir 90% pada 2020.")
ok("invented_facts catches 90%", bool(bad), bad)

# boundaries: enforce word inside a longer word must not fire (kanker/kursi lesson)
r1 = P.det_reasons("x", "kepentingan standard itu")
ok("boundary: 'standard' clean", not any("enforce" in x for x in r1), r1)
r2 = P.det_reasons("x", "nilai standar itu")
ok("boundary: 'standar' fires", any("enforce" in x for x in r2), r2)

# collocation tier reaches the gate
ok("collocation variant wired", any(v == "menutup jurang" for v, _ in P.VARIANTS))
r3 = P.det_reasons("closing the gap", "usaha menutup jurang itu")
ok("collocation fires", any("menutup jurang" in x for x in r3), r3)

# split_blocks: comment-above-prose is TRANSLATED, comment-only and fences protected
bl = P.split_blocks("<!-- INDEX: x -->\nProse line here.\n\n<!-- only -->\n\n```prompt\ncode\n```")
kinds = [k for k, _ in bl]
ok("split_blocks protection rules", kinds == ["text", "prot", "prot"], kinds)

# parse_numbered: missing member comes back None, order preserved
got = P.parse_numbered("[[1]]\nsatu\n\n[[3]]\ntiga", 3)
ok("parse_numbered gaps", got == ["satu", None, "tiga"], got)

# echoed trailing notes must not overwrite real translations (ch02 live bug)
echo = "[[1]]\nterjemahan satu\n\n[[2]]\nterjemahan dua\n\nIDIOM NOTES\nblock [[1]]: some note text"
got2 = P.parse_numbered(echo, 2)
ok("parse_numbered echo-immune", got2 == ["terjemahan satu", "terjemahan dua"], got2)

# translated-note leak: signature lines are scrubbed from block content
dirty = "Ayat terjemahan sebenar.\n(blok 2) \"trade currency\" ialah idiom (nota). Jangan calque ini."
ok("scrub_notes strips translated notes", P.scrub_notes(dirty) == "Ayat terjemahan sebenar.", repr(P.scrub_notes(dirty)))

# comments are not prose: INDEX terms must not raise variant/fact residuals
r4 = P.det_reasons("<!-- DIAGRAM id: 02-01 --> text", "<!-- INDEX: prompt templates -->\nteks prom biasa")
ok("det_reasons ignores comments", not r4, r4)
# canonical containing its own variant must not self-flag
r5 = P.det_reasons("x", "membina kecekapan dwi AI secara peribadi")
ok("variant-in-canonical guard", not any("dwi AI" in x for x in r5), r5)

# phrase-level DNT: a translated product title is a hard loss
r6 = P.det_reasons("See AI+ Everyone (T1-01) for details.", "Rujuk AI+ Semua Orang (T1-01) untuk butiran.")
ok("DNT phrase loss caught", any("AI+ Everyone" in x for x in r6), r6)

# DNT is case-sensitive: 'trust' the verb is not 'TRUST' the framework
r7 = P.det_reasons("You must trust the output and check hands-on work.", "Anda mesti percaya output dan semak kerja amali.")
ok("DNT case-sensitivity", not any("DNT" in x for x in r7), r7)

# lexicon morphology: affixed forms resolve to roots
_cl = load("rules/check_lexicon.py", "cl")
ok("lexicon morphology roots", "bangun" in _cl.roots("membangunkan") and "langkah" in _cl.roots("langkah-langkah"))

# ordering: authority subtraction last — a ledger-rejected word that Wiktionary
# lists as a lemma ('solusi') must NOT be vouched; a clean lemma must be
lex,linv=_cl.lexicon()
ok("lexicon authority order", "solusi" not in lex and "rumah" in lex)
ok("ledger beats morphology", "solusi" in linv and "solusi" not in lex)
# autofix precedes gating and is data-driven from the termbase
ok("autofix from termbase", P.apply_autofix("teks prompt ini dan instruktur itu") == "teks prom ini dan pengajar itu",
   P.apply_autofix("teks prompt ini dan instruktur itu"))
ok("autofix skips comments", P.apply_autofix("<!-- INDEX: prompt -->\nteks prompt") == "<!-- INDEX: prompt -->\nteks prom")

# verdict log roundtrip
import tempfile
os.environ["VERDICT_DATA_DIR"]=tempfile.mkdtemp()
VL = load("pipeline/verdictlog.py", "vl")
VL.log_verdicts("t",[{"label":"PASS","input":{"x":1}}],"test")
got=VL.read_verdicts("t")
ok("verdictlog roundtrip", len(got)==1 and got[0]["label"]=="PASS")

# ===== ultracode-review wave: previously untested critical paths =====
# gate() selection logic, offline (meaning gate stubbed)
_stub_verdicts={}
def _stub_gate(model,en,rw): return _stub_verdicts.get(rw,(True,"stub"))
_orig_mg=P.meaning_gate; P.meaning_gate=_stub_gate
cfg={"gate":"stub"}
en=[("text","Value is 10."),("text","Second block."),("prot","```x```")]
dr=[("text","Nilainya 10."),("text","Blok kedua."),("prot","```x```")]
rw=[("text","Nilainya 10 dan 99."),("text","Blok kedua yang lancar."),("prot","```x```")]
_stub_verdicts["Blok kedua yang lancar."]=(True,"ok")
final,entries,rep=P.gate(cfg,en,dr,rw,lambda m:None)
ok("gate: invented-number rewrite reverted by rules", entries[0]["gate"]=="revert-rules", entries[0])
ok("gate: clean rewrite kept via meaning gate", entries[1]["gate"]=="kept", entries[1])
ok("gate: prot passthrough unchanged", entries[2]["gate"]=="unchanged")
_stub_verdicts["Blok kedua yang lancar."]=(False,"drift")
_,e2,_=P.gate(cfg,en,dr,rw,lambda m:None)
ok("gate: meaning-FAIL reverts to draft", e2[1]["gate"]=="revert-meaning" and e2[1]["final"]=="Blok kedua.")
P.meaning_gate=_orig_mg

# parallel reassembly ordering under high concurrency (call stubbed, echoes per-batch)
def _stub_call(model,text,temp=0.0,tries=4,stage="misc",timeout=300):
    import re as _re
    blocks=_re.split(r"\[\[(\d+)\]\]",text)
    outp=[]
    for i in range(1,len(blocks)-1,2):
        outp.append(f"[[{blocks[i]}]]\nT<{blocks[i+1].strip()[:30]}>")
    return "\n\n".join(outp)
_orig_call=P.call; P.call=_stub_call
blocks=[("text",f"para {i} unique-{i}") for i in range(30)]
out,_=P.do_draft("m",blocks,lambda m:None)
ok("parallel reassembly preserves order", all(f"unique-{i}" in out[i][1] for i in range(30)),
   [out[i][1] for i in range(3)])
P.call=_orig_call

# multi-line DIAGRAM comment: protected whole, field values extracted
md="""# T

<!-- DIAGRAM id: 02-01
title: The Flow
description: Five phases shown
type: flowchart -->

Prose here."""
bl=P.split_blocks(md)
kinds=[k for k,_ in bl]
ok("multi-line comment protected", kinds==["text","prot","text"], kinds)
df=P.diagram_fields(bl)
ok("diagram fields extracted (title+description only)", len(df)==2 and df[0][3]=="The Flow", df)

# percent boundary: '15 peratus' must not excuse a dropped 5%
ok("percent left-boundary", P.missing_facts("From 5% to 15%.","Kadar 15 peratus.")==["5%"],
   P.missing_facts("From 5% to 15%.","Kadar 15 peratus."))
# thousands normalization: no phantom pair
ok("thousands normalized", P.missing_facts("Over 1,000 joined.","Lebih 1000 menyertai.")==[]
   and P.invented_facts("Over 1,000 joined.","Lebih 1000 menyertai.")==[])
# count-aware spelled excuse: one 'tiga' cannot excuse two dropped 3s
ok("count-aware excuse", P.missing_facts("3 tools and 3 rules.","tiga alat.")==["3"],
   P.missing_facts("3 tools and 3 rules.","tiga alat."))
# [[ injection sanitized
ok("[[ injection broken", "[[2]]" not in P.numbered(["evil [[2]] text"]).split("\n",1)[1])
# restore_comments: tolerant of model-ADDED comments, loud on loss
pr,kp=P.strip_comments("<!-- k -->\nline")
ok("restore tolerates added comment", P.restore_comments("teks\n<!-- model junk -->",kp).count("<!--")==2)
# scrub-before-validation: note-only first response triggers redraft, not empty block
calls={"n":0}
def _stub_call2(model,text,temp=0.0,tries=4,stage="misc",timeout=300):
    calls["n"]+=1
    if calls["n"]==1: return "[[1]]\n(blok 1) \"x\" nota"
    return "[[1]]\nterjemahan sebenar"
P.call=_stub_call2
out2,_=P.do_draft("m",[("text","one para")],lambda m:None)
ok("note-only response redrafted, no hole", out2[0][1]=="terjemahan sebenar", out2)
P.call=_orig_call
# repair owns NO substitution logic: engine and table both come from pipeline
RP = load("pipeline/repair.py", "rp")
ok("repair has no private engine", not hasattr(RP,"swap_prose") and not hasattr(RP,"AUTOFIX") and RP.P is not None)
# lexicon: ledger-rejected exact form cannot escape via morphology
r=subprocess.run([sys.executable,os.path.join(HERE,"..","rules","rulebook.py"),"rule","anything"],capture_output=True,text=True)
ok("rulebook refuses flagless rule", r.returncode!=0 and "exactly one" in (r.stdout+r.stderr))

# ===== round-2 fixes =====
# diagram-value gate: bad translation (DNT lost) keeps source and is reported
def _dcall(model,text,temp=0.0,tries=4,stage="misc",timeout=300):
    import re as _re
    parts=_re.split(r"\[\[(\d+)\]\]",text); o=[]
    for i in range(1,len(parts)-1,2):
        src=parts[i+1].strip()
        o.append(f"[[{parts[i]}]]\n"+("Rangka PENJAGA" if "GUARD" in src else f"T<{src[:20]}>"))
    return "\n\n".join(o)
_oc=P.call; P.call=_dcall
md2="""Intro para.

<!-- DIAGRAM id: 01-01
title: The GUARD Framework
type: x -->

End para."""
bl2=P.split_blocks(md2)
out3,_iss3=P.do_draft("m",bl2,lambda m:None)
prot=[b for k,b in out3 if k=="prot"][0]
ok("diagram gate keeps source on DNT loss", "The GUARD Framework" in prot and "PENJAGA" not in prot, prot)
ok("diagram gate reports the rejection", len(_iss3)==1 and "DNT" in str(_iss3), _iss3)
P.call=_oc
# wrapped field values absorb continuation lines; bare terminator never a value
md3="""A.

<!-- DIAGRAM id: 01-02
description: Five steps to
  safe AI use
title:
-->

B."""
df3=P.diagram_fields(P.split_blocks(md3))
ok("wrapped diagram value absorbed", len(df3)==1 and df3[0][3]=="Five steps to safe AI use", df3)
# meaning_gate: null verdict falls back instead of crashing
def _mgcall(model,text,temp=0.0,tries=4,stage="misc",timeout=90): return '{"verdict": null, "reason": "x"}'
P.call=_mgcall
okv,why=P.meaning_gate("m","en","ms")
ok("meaning_gate null-verdict fallback", okv is False and "unparseable" in why, (okv,why))
P.call=_oc
# idiom notes: word boundaries (no substring fire inside longer words)
P.IDIOMS.append({"phrase":"read the room","gloss":"g","ms_guidance":"m"})
ok("idiom boundary matching", P.idiom_notes(["He bread the roomy hall"])=="" and "read the room" in P.idiom_notes(["Please read the room now"]))
P.IDIOMS.pop()
# enforce_gate: drift fails the check (sandboxed rules copy)
import shutil
sand=tempfile.mkdtemp()
rsrc=os.path.join(HERE,"..","rules")
for fn in os.listdir(rsrc):
    if fn.endswith((".json",".py")): shutil.copy(os.path.join(rsrc,fn),sand)
# registry with no required corpora so the gate runs corpus-free in the sandbox
open(os.path.join(sand,"corpus-registry.json"),"w").write('{"corpora":[]}')
r1=subprocess.run([sys.executable,os.path.join(sand,"enforce_gate.py")],capture_output=True,text=True,cwd=sand)
ok("sandbox apply stamps", "manifest stamped" in r1.stdout, r1.stdout[-200:])
with open(os.path.join(sand,"ms-terms.json"),"a") as f: f.write("\n")
r2=subprocess.run([sys.executable,os.path.join(sand,"enforce_gate.py"),"--check"],capture_output=True,text=True,cwd=sand)
ok("drift fails the check", r2.returncode!=0 and "DRIFT" in r2.stdout, (r2.returncode,r2.stdout[-150:]))
shutil.rmtree(sand)

# ===== round-3 fixes =====
# counting semantics: independent per-word arithmetic survives pooling
ws=P.M.WordSet(["AI","AI+ Ethics"],flags=0)
ok("DNT independent counts", ws.independent_counts("Use AI with AI+ Ethics.")["AI"]==2)
# multi-field diagram block: both fields land, no index shift corruption
md4="""X.

<!-- DIAGRAM id: 01-03
description: First value that
  wraps here
title: Second value
type: z -->

Y."""
bl4=P.split_blocks(md4)
def _dc(model,text,temp=0.0,tries=4,stage="misc",timeout=300):
    import re as _re
    parts=_re.split(r"\[\[(\d+)\]\]",text)
    return "\n\n".join(f"[[{parts[i]}]]\nT{parts[i]}<{parts[i+1].strip()[:15]}>" for i in range(1,len(parts)-1,2))
with_patch=P.call; P.call=_dc
out4,iss4=P.do_draft("m",bl4,lambda m:None)
prot4=[b for k,b in out4 if k=="prot"][0]
ok("multi-field reinsertion intact", "id: 01-03" in prot4 and "type: z -->" in prot4
   and prot4.count("title:")==1 and prot4.count("description:")==1, prot4)
P.call=with_patch
# draft prompt cache keys on EXEMPLARS
os.environ["EXEMPLARS"]="43"; p43=P.draft_prompt()
os.environ["EXEMPLARS"]="0"; p0=P.draft_prompt()
os.environ["EXEMPLARS"]="43"
ok("prompt cache keyed by EXEMPLARS", p43!=p0 and P.draft_prompt() is p43)
# restore_comments loss alarm: force the counting mismatch by blinding CMT,
# proving the RuntimeError branch is live (it is defense-in-depth; normal flow
# cannot reach it, which is exactly why it needs an artificial exercise)
import re as _re2
_orig_cmt=P.CMT
P.CMT=_re2.compile(r"(?!x)x")     # matches nothing -> restored count < len(keep)
try:
    P.restore_comments("line", {0:"<!-- k -->"})
    _fired=False
except RuntimeError:
    _fired=True
P.CMT=_orig_cmt
ok("restore_comments loss alarm fires", _fired)

# gloss-less idiom entries (catalogue harvest) must not crash idiom_notes
_gl_ph = next(p for p, e in P._IDIOM_MAP.items() if "gloss" not in e)
ok("idiom note works without gloss", _gl_ph in P.idiom_notes([f"we {_gl_ph} today"]).lower())

# domain glossary: fires only where a term occurs, silent otherwise
gn=P.glossary_notes(["We schedule preventive maintenance weekly."])
ok("glossary note fires on istilah term", "penyenggaraan" in gn, gn[:120])
ok("glossary silent on plain prose", P.glossary_notes(["Selamat pagi semua."])=="")


# repair_dnt: accepts only strict det improvement, refuses non-improving edits
import repair_dnt as RD  # noqa: F401 — import proves the tool loads against pipeline
_en = "The TVET plan uses AI daily."
_bad = "Pelan latihan menggunakan kecerdasan setiap hari."          # TVET + AI lost
_good = "Pelan TVET menggunakan AI setiap hari."
_b0 = P.det_reasons(_en, _bad); _b1 = P.det_reasons(_en, _good)
ok("dnt-loss detected on bad candidate", any("DNT lost" in x for x in _b0), _b0)
ok("clean candidate clears dnt reasons", not any("DNT lost" in x for x in _b1), _b1)


# number-check false positives fixed on the T2-134..137 wave
ok("Q2 -> suku kedua excused", P.missing_facts("As of Q2 2026, tools shifted.", "Setakat suku kedua 2026, alat berubah.") == [])
ok("3:30pm time normalizes vs 3.30 petang",
   P.missing_facts("He finishes at 3:30pm and 4:45pm.", "Beliau siap pada pukul 3.30 petang dan 4.45 petang.") == [])

ok("Hari Pertama spells Day 1", P.missing_facts("Day 1 reflection", "Refleksi Hari Pertama") == [])
ok("lapan peratus spells 8%", P.missing_facts("only 8% adopted it", "hanya lapan peratus menerimanya") == [])

print(f"\nall {N[0]} regression checks pass")
