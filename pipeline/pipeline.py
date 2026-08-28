#!/usr/bin/env python3
"""English -> Malay chapter pipeline: draft -> monolingual rewrite -> gated select.

Single canonical module; all review-verified fixes live here (comment
conservation, in-memory alignment + blocks.json sidecar, bidirectional facts
with time/% normalization, boundary matching everywhere, verified-rules gate).

P0 workflow optimizations (2026-08-29):
  - Intra-chapter parallelism: draft batches, rewrite batches, and meaning-gate
    calls are mutually independent and now run concurrently under a semaphore
    (PIPELINE_CONCURRENCY, default 6). Reassembly is by index, which the
    numbered-block protocol guarantees.
  - Usage telemetry: every call's token usage accumulates per stage and lands
    in the chapter report with an estimated cost. The 23x reasoning-token
    discovery was made by manual probing; telemetry makes that class of
    anomaly visible on run one.
  - Text mechanics (masking, numbers, boundaries) import from rules/msml.py —
    the shared single source of truth.

Usage:
  pipeline.py run <en-file.md> --out <dir> [--config budget|premium] [--name ch01]
Env: OPENROUTER_API_KEY (required), MS_RULES_DIR, EXEMPLARS (default 43),
     PIPELINE_CONCURRENCY (default 6).
"""
import argparse, collections, concurrent.futures, importlib.util, json, os, re
import sys, threading, time, urllib.request, pathlib

_HERE = os.path.dirname(os.path.abspath(__file__))
RULES = os.environ.get("MS_RULES_DIR", os.path.join(_HERE, "..", "rules"))
_spec = importlib.util.spec_from_file_location("msml", os.path.join(RULES, "msml.py"))
M = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(M)

TERMS = json.load(open(os.path.join(RULES, "ms-terms.json")))
BLOCK = json.load(open(os.path.join(RULES, "ms-indonesian-blocklist.json")))
TESTSET = json.load(open(os.path.join(_HERE, "..", "eval", "arbiter", "testset.json")))
try:
    IDIOMS = json.load(open(os.path.join(RULES, "en-idioms.json")))["idioms"]
except FileNotFoundError:
    IDIOMS = []
try:
    _COLL = json.load(open(os.path.join(RULES, "ms-collocations.json")))["collocations"]
except FileNotFoundError:
    _COLL = []

CFG = {
    "budget":  {"draft": "qwen/qwen3.5-397b-a17b", "rewrite": "google/gemma-4-26b-a4b-it", "gate": "google/gemma-4-26b-a4b-it"},
    "premium": {"draft": "anthropic/claude-sonnet-5", "rewrite": "google/gemma-4-26b-a4b-it", "gate": "google/gemini-2.5-flash"},
}
# $/1M tokens (in, out) for the report's cost estimate; update when models change.
PRICE = {"qwen/qwen3.5-397b-a17b": (0.39, 2.34), "anthropic/claude-sonnet-5": (2.00, 10.00),
         "google/gemma-4-26b-a4b-it": (0.07, 0.34), "google/gemini-2.5-flash": (0.30, 2.50)}
DNT = ["AI", "TVET", "PRISM", "TRUST", "BENCH", "HANDS", "GUARD", "ChatGPT", "Claude",
       "Copilot", "Intel", "UNESCO", "ILO", "ITE", "BIBB", "RTO"]
ENFORCE = {e["avoid_id"].lower() for e in BLOCK.get("enforce", [])}
VARIANTS = [(v, t["canonical"]) for t in TERMS["terms"] for v in t.get("variants", [])]
VARIANTS += [(v, c["canonical"]) for c in _COLL if c.get("status") == "enforced" for v in c.get("variants", [])]
CMT = M.CMT
CONCURRENCY = int(os.environ.get("PIPELINE_CONCURRENCY", "6"))

# ---------------- model I/O with telemetry ----------------
USAGE = collections.Counter()
_ULOCK = threading.Lock()
_SEM = threading.Semaphore(CONCURRENCY)

def call(model, text, temp=0.0, tries=4, stage="misc", timeout=300):
    key = os.environ["OPENROUTER_API_KEY"]
    # reasoning off everywhere: 98% of Qwen3.5 output was billed reasoning tokens.
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": text}],
                       "temperature": temp, "reasoning": {"enabled": False}}).encode()
    last = ""
    for a in range(tries):
        try:
            with _SEM:
                r = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=body,
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
                with urllib.request.urlopen(r, timeout=timeout) as f:
                    d = json.load(f)
            u = d.get("usage", {})
            with _ULOCK:
                USAGE[f"{stage}:in"] += u.get("prompt_tokens", 0)
                USAGE[f"{stage}:out"] += u.get("completion_tokens", 0)
                USAGE[f"{stage}:calls"] += 1
                pin, pout = PRICE.get(model, (0, 0))
                USAGE["est_cost_musd"] += int(u.get("prompt_tokens", 0) * pin + u.get("completion_tokens", 0) * pout)
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
    """Reinsert comments at their original indices. The loop bound covers
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
    # A model may echo the IDIOM NOTES section despite instructions (live bug,
    # ch02). Cut the response at the notes heading so echoed guidance can neither
    # spawn duplicate [[n]] markers nor be absorbed into the last block's text.
    raw = re.split(r"\n\s*IDIOM NOTES\b", raw)[0]
    parts = re.split(r"\[\[(\d+)\]\]", raw)
    got = {}
    for i in range(1, len(parts) - 1, 2):
        try:
            k = int(parts[i])
        except ValueError:
            continue
        # FIRST occurrence wins: a model that echoes trailing notes containing
        # [[n]] markers must not overwrite the real translation (live bug, ch02).
        if k not in got:
            got[k] = parts[i + 1].strip()
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
- Where the English verb is "integrate/embed", use "mengintegrasikan" (not "menggunakan"); where English says "adoption" of a technology, prefer "penerimagunaan"; where English says "discipline" (academic), use "disiplin". Plain "use/field/trade" keep menggunakan/bidang. (Site-audit-backed; drafting guidance, not blind replacement.)
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

# ---------------- deterministic checks (mechanics via msml) ----------------
nums, has_word, MW, EN_NUM = M.nums, M.has_word, M.MW, M.EN_NUM

def missing_facts(en_b, t):
    """EN numbers absent from t, excusing 0-9 spelled out, % as 'peratus', X.5 as 'setengah'."""
    miss = list((collections.Counter(nums(en_b)) - collections.Counter(nums(t))).elements())
    out = []
    for n in miss:
        if n in MW and has_word(t, MW[n]):
            continue
        if n.endswith("%") and re.search(rf"{re.escape(n[:-1])}\s*(%|peratus)", t, re.I):
            continue
        if re.fullmatch(r"\d+\.5", n) and has_word(t, "setengah"):
            continue
        out.append(n)
    return out

def invented_facts(en_b, t):
    """Numbers in t whose VALUE the English never contained (bidirectional fact gate)."""
    en_vals = set(nums(en_b))
    for w, v in EN_NUM.items():
        if has_word(en_b, w):
            en_vals.add(v)
    en_vals |= {v.rstrip("%") for v in en_vals}
    return [n for n in set(nums(t)) if n not in en_vals and n.rstrip("%") not in en_vals]

def det_reasons(en_b, cand):
    """Rule violations of one candidate measured against the ENGLISH + the verified
    rules layer. Never measured against a sibling draft."""
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
        raw = call(model, p, temp=0.0, stage="gate", timeout=90)
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                d = json.loads(m.group(0))
                return d.get("verdict", "FAIL").upper() == "PASS", d.get("reason", "")
            except json.JSONDecodeError:
                pass
        time.sleep(2)
    return False, "unparseable gate response (2 tries)"

# ---------------- stages (parallel within each stage) ----------------
NOTE_SIG = re.compile(r"^\s*\(blo\w*\s+\d+\)|ialah idiom|is an idiom|IDIOM NOTES|Jangan calque", re.I)

def scrub_notes(text):
    """Models sometimes translate the idiom guidance into Malay and emit it as
    content (live bug, ch02 — twice, two different leak paths). The note text is
    ours, so its signature is scrubbable deterministically."""
    return "\n".join(l for l in text.split("\n") if not NOTE_SIG.search(l)).strip()

def idiom_notes(chunk):
    """Per-batch calque prevention: name each English idiom present and how to
    render its MEANING. Fires only where an idiom actually occurs."""
    notes = []
    for j, c in enumerate(chunk):
        low = c.lower()
        for it in IDIOMS:
            if it["phrase"] in low:
                notes.append(f'(block {j+1}) "{it["phrase"]}" is an idiom ({it["gloss"]}). {it["ms_guidance"]}')
    if not notes:
        return ""
    return ("\n\nIDIOM NOTES — render the meaning, never the words; do not echo these notes:\n"
            + "\n".join(notes[:12]))

def do_draft(model, blocks, log):
    src = [b for k, b in blocks if k == "text"]
    stripped = [strip_comments(b) for b in src]
    texts = [pr for pr, _ in stripped]
    B = 8
    batches = [(i, texts[i:i + B]) for i in range(0, len(texts), B)]

    def one_batch(arg):
        i, chunk = arg
        got = parse_numbered(call(model, draft_prompt() + idiom_notes(chunk) + "\n\n" + numbered(chunk),
                                  temp=0.3, stage="draft"), len(chunk))
        out = []
        for j, g in enumerate(got):
            for _ in range(3):
                if g is not None and g.strip():
                    break
                g = (parse_numbered(call(model, draft_prompt() + "\n\n" + numbered([chunk[j]]),
                                         temp=0.3, stage="draft"), 1) or [None])[0]
            if not (g and g.strip()):
                # Falling back to English is how 799 words once shipped untranslated.
                raise RuntimeError(f"draft failed for block {i + j} after retries; refusing to emit English")
            out.append(scrub_notes(g))
        log(f"draft batch {i // B + 1}/{len(batches)} done")
        return i, out

    results = {}
    with concurrent.futures.ThreadPoolExecutor(CONCURRENCY) as ex:
        for i, out in ex.map(one_batch, batches):
            results[i] = out
    flat = [t for i in sorted(results) for t in results[i]]
    flat = [restore_comments(t, keep) for t, (_, keep) in zip(flat, stripped)]
    it = iter(flat)
    return [(k, b) if k == "prot" else ("text", next(it)) for k, b in blocks]

def do_rewrite(model, blocks, log):
    texts = [(i, b) for i, (k, b) in enumerate(blocks) if k == "text"]
    meta = {i: strip_comments(b) for i, b in texts}
    B = 8
    batches = [texts[s:s + B] for s in range(0, len(texts), B)]

    def one_batch(chunk):
        got = parse_numbered(call(model, RW + "\n\n" + numbered([meta[i][0] for i, _ in chunk]),
                                  temp=0.0, stage="rewrite"), len(chunk))
        res = {}
        for (idx, orig), g in zip(chunk, got):
            if g and g.strip():
                # a failed rewrite falls back to the DRAFT (already Malay), never English
                res[idx] = restore_comments(g, meta[idx][1])
            else:
                res[idx] = orig
                log(f"rewrite fallback on block {idx} (parse failure) — draft kept")
        return res

    out = {}
    with concurrent.futures.ThreadPoolExecutor(CONCURRENCY) as ex:
        for res in ex.map(one_batch, batches):
            out.update(res)
    log(f"rewrite {len(texts)}/{len(texts)}")
    return [(k, out.get(i, b) if k == "text" else b) for i, (k, b) in enumerate(blocks)]

def gate(cfg, en_blocks, dr_blocks, rw_blocks, log):
    """Select the better of {draft, rewrite} per block, both scored against the
    English; deterministic decisions first, meaning-gate calls run in parallel."""
    entries = [None] * len(en_blocks)
    need_llm = []
    for i, ((k, e), (_, d), (_, r)) in enumerate(zip(en_blocks, dr_blocks, rw_blocks)):
        if k == "prot" or r.strip() == d.strip():
            entries[i] = {"i": i, "kind": k, "en": e, "draft": d, "rewrite": None,
                          "final": d, "gate": "unchanged"}
            continue
        dd, rd = det_reasons(e, d), det_reasons(e, r)
        if len(rd) > len(dd):
            entries[i] = {"i": i, "kind": k, "en": e, "draft": d, "rewrite": r, "final": d,
                          "gate": "revert-rules", "why": f"draft={dd or 'clean'} rewrite={rd}"}
            continue
        need_llm.append((i, e, d, r, dd, rd))

    def one(arg):
        i, e, d, r, dd, rd = arg
        ok, why = meaning_gate(cfg["gate"], e, r)
        repaired = ok and len(rd) < len(dd)
        if repaired:
            why = f"REPAIRED draft {dd} -> {rd}"
        return i, {"i": i, "kind": "text", "en": e, "draft": d, "rewrite": r,
                   "final": r if ok else d, "gate": "kept" if ok else "revert-meaning",
                   "why": why}, repaired

    repaired_n = 0
    with concurrent.futures.ThreadPoolExecutor(CONCURRENCY) as ex:
        for i, entry, rep in ex.map(one, need_llm):
            entries[i] = entry
            repaired_n += rep
    log(f"gate: {len(need_llm)} meaning checks done")
    final = [(x["kind"], x["final"]) for x in entries]
    return final, entries, repaired_n

def run(en_path, outdir, config, name):
    cfg = CFG[config]
    out = pathlib.Path(outdir); out.mkdir(parents=True, exist_ok=True)
    log = lambda m: print(f"    [{name}] {m}", flush=True)
    t0 = time.time()
    en_blocks = split_blocks(pathlib.Path(en_path).read_text(encoding="utf8"))
    log(f"{len(en_blocks)} blocks ({sum(1 for k, _ in en_blocks if k == 'text')} translatable), concurrency={CONCURRENCY}")
    dr = do_draft(cfg["draft"], en_blocks, log)
    (out / f"{name}-draft.md").write_text(join_blocks(dr), encoding="utf8")
    rw = do_rewrite(cfg["rewrite"], dr, log)
    (out / f"{name}-rewrite.md").write_text(join_blocks(rw), encoding="utf8")
    final, entries, repaired = gate(cfg, en_blocks, dr, rw, log)
    (out / f"{name}-final.md").write_text(join_blocks(final), encoding="utf8")
    # alignment persisted: regating/judging later reads this, never re-splits from disk
    json.dump(entries, open(out / f"{name}-blocks.json", "w"), ensure_ascii=False, indent=1)
    changed = [x for x in entries if x["gate"] != "unchanged"]
    kept = sum(1 for x in changed if x["gate"] == "kept")
    residual = [{"i": x["i"], "issues": det_reasons(x["en"], x["final"])}
                for x in entries if x["kind"] == "text"]
    residual = [r for r in residual if r["issues"]]
    usage = {k: v for k, v in USAGE.items() if k != "est_cost_musd"}
    rep = {"name": name, "config": config, "models": cfg,
           "blocks": len(en_blocks), "changed": len(changed), "kept": kept,
           "reverted": len(changed) - kept, "repaired": repaired,
           "residual_rule_issues": residual, "seconds": round(time.time() - t0),
           "usage": usage, "est_cost_usd": round(USAGE["est_cost_musd"] / 1e6, 4)}
    json.dump(rep, open(out / f"{name}-report.json", "w"), ensure_ascii=False, indent=1)
    log(f"done: changed={len(changed)} kept={kept} reverted={len(changed)-kept} "
        f"repaired={repaired} residual={len(residual)} "
        f"(${rep['est_cost_usd']:.3f}, {rep['seconds']}s)")
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
