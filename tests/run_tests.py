#!/usr/bin/env python3
"""Zero-dependency regression suite. Every case here is a bug that shipped once."""
import os, sys, importlib.util
os.environ.setdefault("OPENROUTER_API_KEY", "dummy")
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("p", os.path.join(HERE, "..", "pipeline", "pipeline.py"))
P = importlib.util.module_from_spec(spec); spec.loader.exec_module(P)
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
import importlib.util as _iu
_ls=_iu.spec_from_file_location("cl", os.path.join(HERE,"..","rules","check_lexicon.py"))
# import module functions without running main
import types
_src=open(os.path.join(HERE,"..","rules","check_lexicon.py")).read().replace("\nmain()","\n")
_cl=types.ModuleType("cl"); _cl.__dict__["__file__"]=os.path.join(HERE,"..","rules","check_lexicon.py"); exec(compile(_src,"check_lexicon.py","exec"),_cl.__dict__)
ok("lexicon morphology roots", "bangun" in _cl.roots("membangunkan") and "langkah" in _cl.roots("langkah-langkah"))

# ordering: authority subtraction last — a ledger-rejected word that Wiktionary
# lists as a lemma ('solusi') must NOT be vouched; a clean lemma must be
lex=_cl.lexicon()
ok("lexicon authority order", "solusi" not in lex and "rumah" in lex)
# autofix precedes gating and is data-driven from the termbase
ok("autofix from termbase", P.apply_autofix("teks prompt ini dan instruktur itu") == "teks prom ini dan pengajar itu",
   P.apply_autofix("teks prompt ini dan instruktur itu"))
ok("autofix skips comments", P.apply_autofix("<!-- INDEX: prompt -->\nteks prompt") == "<!-- INDEX: prompt -->\nteks prom")

# verdict log roundtrip
import tempfile
os.environ["VERDICT_DATA_DIR"]=tempfile.mkdtemp()
_vs=_iu.spec_from_file_location("vl", os.path.join(HERE,"..","pipeline","verdictlog.py"))
VL=_iu.module_from_spec(_vs); _vs.loader.exec_module(VL)
VL.log_verdicts("t",[{"label":"PASS","input":{"x":1}}],"test")
got=VL.read_verdicts("t")
ok("verdictlog roundtrip", len(got)==1 and got[0]["label"]=="PASS")

print(f"\nall {N[0]} regression checks pass")
