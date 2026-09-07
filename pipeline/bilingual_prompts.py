#!/usr/bin/env python3
"""Bilingual prompt bodies: Malay body first, English original beneath.

Ruling 2026-09-07 (operator): ```prompt fences are the one protected block that IS
translated. MS-41 kept them English because "prompt text is what the reader types";
in practice a Malay reader was handed forty blocks of English in a Malay book. The
Malay body goes through the same draft -> rewrite -> gate sieve as prose (facts, DNT,
terms, meaning), and the English original stays verbatim beneath so the prompt still
works as typed. Fail-toward-source holds: a body the gate cannot clear stays English
as a single fence, and is reported.

Usage: bilingual_prompts.py <bundle-dir> [--config budget]     (OPENROUTER_API_KEY)
Writes the chapter files in place and <bundle>/bilingual-prompts-report.json.
"""
import glob, json, os, re, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import pipeline as P

ADDENDUM = """

PROMPT BLOCKS: every numbered block below is an AI PROMPT the reader will type into an AI tool.
- Translate it into natural Malay in the same register. Where the English opens "You are a ...", the AI is being addressed: write "Anda ialah ..." / "Anda seorang ...".
- Keep every [bracketed placeholder] in square brackets and translate its wording ([your trade] -> [bidang anda]); keep [X], numbers, units, standard codes, product names and every do-not-translate term verbatim.
- Keep line breaks, numbering and list structure exactly. Do not add explanations, notes or headings."""
_orig_dp = P.draft_prompt
P.draft_prompt = lambda: _orig_dp() + ADDENDUM
P.RW = P.RW + ADDENDUM
LABEL = "*Asal Inggeris:*"
FENCE = re.compile(r"```prompt\n(.*?)```", re.S)

def process(md, cfg, log):
    fences = list(FENCE.finditer(md))
    # idempotent: a fence preceded by the label is the English original; a fence FOLLOWED by
    # the label is an already-translated Malay body — neither is a source (a trial run once
    # re-translated a Malay body into a second Malay body)
    todo = [m for m in fences
            if not md[max(0, m.start() - 60):m.start()].rstrip().endswith(LABEL)
            and not md[m.end():m.end() + 40].lstrip().startswith(LABEL)]
    if not todo:
        return md, 0, []
    bodies = [m.group(1).rstrip("\n") for m in todo]
    en_blocks = [("text", b) for b in bodies]
    dr, _ = P.do_draft(cfg["draft"], en_blocks, log)
    rw = P.do_rewrite(cfg["rewrite"], dr, log)
    final, entries, _ = P.gate(cfg, en_blocks, dr, rw, log)
    out, last, n, issues = [], 0, 0, []
    for m, (_, ms), e in zip(todo, final, entries):
        ms = ms.rstrip("\n"); en = m.group(1).rstrip("\n")
        if ms.strip() == en.strip():
            issues.append({"prompt": en[:70], "kept": "english", "why": (e or {}).get("why")})
            out.append(md[last:m.end()]); last = m.end(); continue
        out.append(md[last:m.start()] + f"```prompt\n{ms}\n```\n\n{LABEL}\n\n```prompt\n{en}\n```")
        last = m.end(); n += 1
        if (e or {}).get("final_det"):
            issues.append({"prompt": en[:70], "residual": e["final_det"]})
    out.append(md[last:])
    return "".join(out), n, issues

def main():
    bundle = sys.argv[1]; config = sys.argv[sys.argv.index("--config") + 1] if "--config" in sys.argv else "budget"
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    cfg = P.CFG[config]; report = {"bundle": bundle, "files": {}, "started": time.strftime("%Y-%m-%d %H:%M")}
    total = 0
    for f in sorted(glob.glob(os.path.join(bundle, "*.md"))):
        if only and only not in os.path.basename(f): continue
        md = open(f, encoding="utf8").read()
        if "```prompt" not in md: continue
        name = os.path.basename(f)[:24]; log = lambda m, name=name: print(f"    [{name}] {m}", flush=True)
        new, n, issues = process(md, cfg, log)
        if n:
            P.atomic_write(f, new)
        report["files"][os.path.basename(f)] = {"bilingual": n, "issues": issues}; total += n
        log(f"{n} prompt(s) made bilingual, {len(issues)} issue(s)")
    report["total_bilingual"] = total
    json.dump(report, open(os.path.join(bundle, "bilingual-prompts-report.json"), "w"), ensure_ascii=False, indent=1)
    print(f"done: {total} prompts bilingual · report written")

if __name__ == "__main__":
    main()
