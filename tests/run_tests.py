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

print(f"\nall {N[0]} regression checks pass")
