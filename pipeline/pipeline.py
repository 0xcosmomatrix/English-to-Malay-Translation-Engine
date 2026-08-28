#!/usr/bin/env python3
"""English -> Malay chapter pipeline: draft -> monolingual rewrite -> gated select.

Single canonical module. The 2026-08-28 adversarial review found fixes stranded
across sibling scripts (regate.py, gate_v3.py) while this entrypoint ran the old
buggy gate; those siblings are gone and every fix lives here:

  1. restore_comments no longer drops comment lines when a translation has fewer
     lines than its source (visits every comment index; conservation-asserted).
  2. No re-splitting from disk for gating: blocks stay aligned in memory
     end-to-end and are persisted to <name>-blocks.json. Re-gating reads the
     sidecar; there is no count-mismatch zip to misalign silently.
  3. Fact scoring is bidirectional: a candidate is penalized for numbers the
     English never contained (hallucinated values), not only for missing ones.
     Spelled-out English numbers ("three" -> "3"/"tiga") are excused.
  4. All phrase matching is word-boundary based, multi-word included.
  5. The deterministic gate uses the verified rules layer: the blocklist's
     enforce tier (193 dictionary-verified entries) and termbase variants —
     never the advisory flag tier, and never a hand-written word list.

Usage:
  pipeline.py run <en-file.md> --out <dir> [--config budget|premium] [--name ch01]
Env: OPENROUTER_API_KEY (required), MS_RULES_DIR, EXEMPLARS (default 43).
"""
import argparse, json, os, re, sys, time, urllib.request, pathlib

_HERE = os.path.dirname(os.path.abspath(__file__))
RULES = os.environ.get("MS_RULES_DIR", os.path.join(_HERE, "..", "rules"))
TERMS = json.load(open(os.path.join(RULES, "ms-terms.json")))
BLOCK = json.load(open(os.path.join(RULES, "ms-indonesian-blocklist.json")))
TESTSET = json.load(open(os.path.join(_HERE, "..", "eval", "arbiter", "testset.json")))

CFG = {
    "budget":  {"draft": "qwen/qwen3.5-397b-a17b", "rewrite": "google/gemma-4-26b-a4b-it", "gate": "google/gemma-4-26b-a4b-it"},
    "premium": {"draft": "anthropic/claude-sonnet-5", "rewrite": "google/gemma-4-26b-a4b-it", "gate": "google/gemini-2.5-flash"},
}
DNT = ["AI", "TVET", "PRISM", "TRUST", "BENCH", "HANDS", "GUARD", "ChatGPT", "Claude",
       "Copilot", "Intel", "UNESCO", "ILO", "ITE", "BIBB", "RTO"]
ENFORCE = {e["avoid_id"].lower() for e in BLOCK.get("enforce", [])}
VARIANTS = [(v, t["canonical"]) for t in TERMS["terms"] for v in t.get("variants", [])]
CMT = re.compile(r"^\s*<!--.*?-->\s*$")

# ---------------- model I/O ----------------
def call(model, text, temp=0.0, tries=4):
    key = os.environ["OPENROUTER_API_KEY"]
    # reasoning off everywhere: 98% of Qwen3.5 output was billed reasoning tokens.
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": text}],
                       "temperature": temp, "reasoning": {"enabled": False}}).encode()
    last = ""
    for a in range(tries):
        try:
            r = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=body,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            with urllib.request.urlopen(r, timeout=300) as f:
                d = json.load(f)
            c = d["choices"][0]["message"]["content"]
            if c and c.strip():
                return c
            last = "empty"
        except Exception as e:
            last = str(e)[:120]
        time.sleep(4 * (a + 1))
    raise RuntimeError(f"{model}: {last}")

# ---------------- block model ----------------
def split_blocks(md):
    """Blank-line blocks; fenced code becomes single PROTECTED blocks; a block that
    is ONLY html comments is protected. A comment directly above prose is NOT —
    protecting those once shipped 799 words of English silently."""
    out, buf, infence = [], [], False
    for line in md.splitlines():
        if line.startswith("```"):
            if buf and not infence:
                out.append(("text", "\n".join(buf))); buf = []
            buf.append(line)
            if infence:
                out.append(("prot", "\n".join(buf))); buf = []
            infence = not infence
            continue
        if infence:
            buf.append(line); continue
        if line.strip() == "":
            if buf:
                out.append(("text", "\n".join(buf))); buf = []
        else:
            buf.append(line)
    if buf:
        out.append(("prot" if infence else "text", "\n".join(buf)))
    return [("prot", b) if k == "text" and all(CMT.match(l) or not l.strip() for l in b.split("\n"))
            else (k, b) for k, b in out]

def join_blocks(blocks):
    return "\n\n".join(b for _, b in blocks) + "\n"

def strip_comments(block):
    lines = block.split("\n")
    keep = {i: l for i, l in enumerate(lines) if CMT.match(l)}
    prose = "\n".join(l for i, l in enumerate(lines) if i not in keep)
    return prose, keep

def restore_comments(prose, keep):
    """Reinsert comments at their original indices. Fix #1: the loop bound covers
    max(keep) even when the translation is shorter than the source, so a trailing
    comment can never be dropped; conservation is asserted."""
    if not keep:
        return prose
    plines = prose.split("\n"); out = []; pi = 0
    total = max(len(keep) + len(plines), max(keep) + 1)
    for i in range(total):
        if i in keep:
            out.append(keep[i])
        elif pi < len(plines):
            out.append(plines[pi]); pi += 1
    out.extend(plines[pi:])
    assert sum(1 for l in out if CMT.match(l)) == len(keep), "comment lost in restore"
    return "\n".join(out)

def numbered(chunks):
    return "\n\n".join(f"[[{i+1}]]\n{c}" for i, c in enumerate(chunks))

def parse_numbered(raw, n):
    parts = re.split(r"\[\[(\d+)\]\]", raw)
    got = {}
    for i in range(1, len(parts) - 1, 2):
        try:
            got[int(parts[i])] = parts[i + 1].strip()
        except ValueError:
            pass
    return [got.get(i + 1) for i in range(n)]

# ---------------- prompts ----------------
def draft_prompt():
    trm = "\n".join(f'- "{t["en"]}" -> {t["canonical"]}  (never: {", ".join(t.get("variants", []))})'
                    for t in TERMS["terms"])
    n = int(os.environ.get("EXEMPLARS", "43"))
    real = [i for i in TESTSET if i["truth"] == "REAL"][:n]
    exs = "\n".join("- " + re.sub(r"^\[[a-z]+\] ", "", e["label"]) for e in real) or "(none supplied)"
    return f"""You are a native Malaysian author writing the Bahasa Melayu (Malaysia) edition of a professional book about AI for TVET instructors — not a literal translator. Standard Bahasa Melayu Malaysia, formal but natural; never Indonesian forms.

RULES
- Address the reader as "anda"; instructional we = "kita".
- No comma before "dan" in a series. Use "ialah" before nouns, never "Ini adalah".
- "tool" = "alat" (never "peranti"). "new" = "baharu". Spell numbers 0-9 as words except versions/steps/measurements.
- Keep in English: AI, TVET, PRISM, TRUST, BENCH, HANDS, GUARD, product names (ChatGPT, Claude, Copilot, Intel), institution names, and acronyms (RTO, ITE, BIBB, ILO, UNESCO). Framework letters keep their English word with a Malay gloss in parentheses on first use.
- TERMS (binding):
{trm}

EDITOR PRECEDENT — a Malaysian reviewer corrected an earlier translation of this book series; write in the register these corrections point to:
{exs}

TASK: translate each numbered block below into Malay. Return the SAME numbered blocks [[n]] in the same order, nothing else. Preserve markdown (#, **, lists) exactly. Translate heading text. Do not add or drop sentences; every fact, number and name must survive exactly."""

RW = ("Anda ialah editor buku profesional dari Malaysia. Baiki setiap perenggan bernombor di bawah supaya berbunyi "
      "seperti tulisan asal penulis Malaysia — Bahasa Melayu Malaysia yang baku, formal tetapi lancar. "
      "Betulkan terjemahan harfiah dan susunan ayat yang berbau Inggeris. JANGAN ubah maksud, fakta, angka, nama, "
      "istilah Inggeris yang dikekalkan (AI, TVET, PRISM, TRUST, BENCH, HANDS, GUARD, nama produk dan institusi), "
      "atau struktur markdown (#, **, senarai). Jika sesuatu perenggan sudah baik, kembalikannya tanpa perubahan. "
      "Kembalikan blok bernombor [[n]] yang sama sahaja.")

# ---------------- deterministic checks ----------------
NUM = re.compile(r"\d[\d.,%]*")
MW = {"0": "sifar", "1": "satu", "2": "dua", "3": "tiga", "4": "empat", "5": "lima",
      "6": "enam", "7": "tujuh", "8": "lapan", "9": "sembilan"}
EN_NUM = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
          "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11",
          "twelve": "12", "thirteen": "13", "fourteen": "14", "fifteen": "15", "sixteen": "16",
          "seventeen": "17", "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30",
          "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80",
          "ninety": "90", "hundred": "100", "thousand": "1000", "million": "1000000"}

def nums(s):
    return sorted(re.sub(r"[.,]+$", "", m) for m in NUM.findall(s))

def has_word(t, w):
    return re.search(rf"(?<![\w-]){re.escape(w)}(?![\w-])", t, re.I) is not None

def missing_facts(en_b, t):
    """EN numbers absent from t, excusing 0-9 spelled out in Malay and % as 'peratus'."""
    import collections
    miss = list((collections.Counter(nums(en_b)) - collections.Counter(nums(t))).elements())
    out = []
    for n in miss:
        if n in MW and has_word(t, MW[n]):
            continue
        if n.endswith("%") and re.search(rf"{re.escape(n[:-1])}\s*(%|peratus)", t, re.I):
            continue
        out.append(n)
    return out

def invented_facts(en_b, t):
    """Fix #3: numbers in t whose VALUE the English never contained. Digits that
    render a spelled-out English number are excused; duplicates of present values
    are excused (restating '15,000' twice is not hallucination — inventing 90% is)."""
    en_vals = set(nums(en_b))
    for w, v in EN_NUM.items():
        if has_word(en_b, w):
            en_vals.add(v)
    # normalize %: EN "28%" rendered as "28 peratus" is the same value, both ways
    en_vals |= {v.rstrip("%") for v in en_vals}
    return [n for n in set(nums(t)) if n not in en_vals and n.rstrip("%") not in en_vals]

def det_reasons(en_b, cand):
    """Rule violations of one candidate measured against the ENGLISH + the verified
    rules layer. Never measured against a sibling draft (the v2 lesson)."""
    reasons = []
    m = missing_facts(en_b, cand)
    if m: reasons.append(f"facts missing: {m[:4]}")
    inv = invented_facts(en_b, cand)
    if inv: reasons.append(f"facts invented: {inv[:4]}")
    lost = [d for d in DNT
            if len(re.findall(rf"\b{re.escape(d)}\b", en_b)) > len(re.findall(rf"\b{re.escape(d)}\b", cand))]
    if lost: reasons.append(f"DNT lost: {lost}")
    hard = sorted(w for w in ENFORCE if has_word(cand, w))
    if hard: reasons.append(f"enforce-tier violation: {hard[:4]}")
    tv = [v for v, c in VARIANTS if has_word(cand, v)]
    if tv: reasons.append(f"term variant: {tv[:3]}")
    return reasons

def meaning_gate(model, en_b, rw_b):
    p = (f'ENGLISH ORIGINAL:\n{en_b}\n\nMALAY REVISION:\n{rw_b}\n\n'
         'Does the Malay preserve the FULL meaning of the English — every fact, quantity, negation, '
         'and who-does-what relationship — with NOTHING added that the English does not say? '
         'Style differences are fine. Return STRICT JSON: {"verdict":"PASS"|"FAIL","reason":"<short>"}')
    for _ in range(2):
        raw = call(model, p, temp=0.0)
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                d = json.loads(m.group(0))
                return d.get("verdict", "FAIL").upper() == "PASS", d.get("reason", "")
            except json.JSONDecodeError:
                pass
        time.sleep(2)
    return False, "unparseable gate response (2 tries)"

# ---------------- stages ----------------
def do_draft(model, blocks, log):
    src = [b for k, b in blocks if k == "text"]
    stripped = [strip_comments(b) for b in src]
    texts = [pr for pr, _ in stripped]
    out, B = [], 8
    for i in range(0, len(texts), B):
        chunk = texts[i:i + B]
        got = parse_numbered(call(model, draft_prompt() + "\n\n" + numbered(chunk), temp=0.3), len(chunk))
        for j, g in enumerate(got):
            for _ in range(3):
                if g is not None and g.strip():
                    break
                g = (parse_numbered(call(model, draft_prompt() + "\n\n" + numbered([chunk[j]]), temp=0.3), 1) or [None])[0]
            if not (g and g.strip()):
                raise RuntimeError(f"draft failed for block {i + j} after retries; refusing to emit English")
            out.append(g)
        log(f"draft {min(i + B, len(texts))}/{len(texts)}")
    out = [restore_comments(t, keep) for t, (_, keep) in zip(out, stripped)]
    res, it = [], iter(out)
    return [(k, b) if k == "prot" else ("text", next(it)) for k, b in blocks]

def do_rewrite(model, blocks, log):
    texts = [(i, b) for i, (k, b) in enumerate(blocks) if k == "text"]
    meta = {i: strip_comments(b) for i, b in texts}
    out, B = {}, 8
    for s in range(0, len(texts), B):
        chunk = texts[s:s + B]
        got = parse_numbered(call(model, RW + "\n\n" + numbered([meta[i][0] for i, _ in chunk]), temp=0.0), len(chunk))
        for (idx, orig), g in zip(chunk, got):
            if g and g.strip():
                out[idx] = restore_comments(g, meta[idx][1])
            else:
                out[idx] = orig
                log(f"rewrite fallback on block {idx} (parse failure) — draft kept")
        log(f"rewrite {min(s + B, len(texts))}/{len(texts)}")
    return [(k, out.get(i, b) if k == "text" else b) for i, (k, b) in enumerate(blocks)]

def gate(cfg, en_blocks, dr_blocks, rw_blocks, log):
    """Select the better of {draft, rewrite} per block, both scored against the
    English; ties go to the rewrite (register-improved) via the meaning gate."""
    final, entries, repaired = [], [], 0
    for i, ((k, e), (_, d), (_, r)) in enumerate(zip(en_blocks, dr_blocks, rw_blocks)):
        if k == "prot" or r.strip() == d.strip():
            final.append((k, d))
            entries.append({"i": i, "kind": k, "en": e, "draft": d, "rewrite": None,
                            "final": d, "gate": "unchanged"})
            continue
        dd, rd = det_reasons(e, d), det_reasons(e, r)
        if len(rd) > len(dd):
            final.append((k, d))
            entries.append({"i": i, "kind": k, "en": e, "draft": d, "rewrite": r, "final": d,
                            "gate": "revert-rules", "why": f"draft={dd or 'clean'} rewrite={rd}"})
            continue
        ok, why = meaning_gate(cfg["gate"], e, r)
        if ok and len(rd) < len(dd):
            repaired += 1
            why = f"REPAIRED draft {dd} -> {rd}"
        final.append((k, r if ok else d))
        entries.append({"i": i, "kind": k, "en": e, "draft": d, "rewrite": r,
                        "final": r if ok else d, "gate": "kept" if ok else "revert-meaning", "why": why})
    return final, entries, repaired

def run(en_path, outdir, config, name):
    cfg = CFG[config]
    out = pathlib.Path(outdir); out.mkdir(parents=True, exist_ok=True)
    log = lambda m: print(f"    [{name}] {m}", flush=True)
    t0 = time.time()
    en_blocks = split_blocks(pathlib.Path(en_path).read_text(encoding="utf8"))
    log(f"{len(en_blocks)} blocks ({sum(1 for k, _ in en_blocks if k == 'text')} translatable)")
    dr = do_draft(cfg["draft"], en_blocks, log)
    (out / f"{name}-draft.md").write_text(join_blocks(dr), encoding="utf8")
    rw = do_rewrite(cfg["rewrite"], dr, log)
    (out / f"{name}-rewrite.md").write_text(join_blocks(rw), encoding="utf8")
    final, entries, repaired = gate(cfg, en_blocks, dr, rw, log)
    (out / f"{name}-final.md").write_text(join_blocks(final), encoding="utf8")
    # Fix #2: alignment persisted; regating or judging later reads this, never re-splits.
    json.dump(entries, open(out / f"{name}-blocks.json", "w"), ensure_ascii=False, indent=1)
    changed = [x for x in entries if x["gate"] != "unchanged"]
    kept = sum(1 for x in changed if x["gate"] == "kept")
    residual = [{"i": x["i"], "issues": det_reasons(x["en"], x["final"])}
                for x in entries if x["kind"] == "text"]
    residual = [r for r in residual if r["issues"]]
    rep = {"name": name, "config": config, "models": cfg,
           "blocks": len(en_blocks), "changed": len(changed), "kept": kept,
           "reverted": len(changed) - kept, "repaired": repaired,
           "residual_rule_issues": residual, "seconds": round(time.time() - t0)}
    json.dump(rep, open(out / f"{name}-report.json", "w"), ensure_ascii=False, indent=1)
    log(f"done: changed={len(changed)} kept={kept} reverted={len(changed)-kept} "
        f"repaired={repaired} residual={len(residual)} ({rep['seconds']}s)")
    return rep

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("en_file"); r.add_argument("--out", required=True)
    r.add_argument("--config", default="budget", choices=list(CFG))
    r.add_argument("--name")
    a = ap.parse_args()
    name = a.name or pathlib.Path(a.en_file).stem
    run(a.en_file, a.out, a.config, name)

if __name__ == "__main__":
    main()
