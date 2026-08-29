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
import sys, threading, time, urllib.error, urllib.request, pathlib

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
    print("WARNING: en-idioms.json missing — idiom notes disabled", file=sys.stderr)
try:
    GLOSSARY = json.load(open(os.path.join(RULES, "ms-domain-glossary.json")))["entries"]
except FileNotFoundError:
    GLOSSARY = []
try:
    _COLL = json.load(open(os.path.join(RULES, "ms-collocations.json")))["collocations"]
except FileNotFoundError:
    _COLL = []
    print("WARNING: ms-collocations.json missing — collocation gate disabled", file=sys.stderr)

CFG = {
    "budget":  {"draft": "qwen/qwen3.5-397b-a17b", "rewrite": "google/gemma-4-26b-a4b-it", "gate": "google/gemma-4-26b-a4b-it"},
    "premium": {"draft": "anthropic/claude-sonnet-5", "rewrite": "google/gemma-4-26b-a4b-it", "gate": "google/gemini-2.5-flash"},
}
# $/1M tokens (in, out) for the report's cost estimate; update when models change.
PRICE = {"qwen/qwen3.5-397b-a17b": (0.39, 2.34), "anthropic/claude-sonnet-5": (2.00, 10.00),
         "google/gemma-4-26b-a4b-it": (0.07, 0.34), "google/gemini-2.5-flash": (0.30, 2.50)}
_DNT = json.load(open(os.path.join(RULES, "ms-dnt.json")))
DNT = _DNT["tokens"] + _DNT["phrases"]
ENFORCE = {e["avoid_id"].lower() for e in BLOCK.get("enforce", [])}
VARIANTS = [(v, t["canonical"]) for t in TERMS["terms"] for v in t.get("variants", [])]
VARIANTS += [(v, c["canonical"]) for c in _COLL if c.get("status") == "enforced" for v in c.get("variants", [])]
CMT = M.CMT
CONCURRENCY = int(os.environ.get("PIPELINE_CONCURRENCY", "6"))
sys.path.insert(0, _HERE)
try:
    import verdictlog as _VL
except Exception:
    _VL = None

def safe_name(stem):
    """Chapter identity: ONE derivation, imported by run_book — the filename is
    a cross-process protocol and copies of this rule drifted once."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", stem)

# ---- precompiled rule matchers (the det-gate hot path) ----
_ENF_WS = M.WordSet(sorted(ENFORCE))
_DNT_WS = M.WordSet(DNT, flags=0)                      # case-sensitive: proper names
_VAR_RX = [(re.compile(M.word_pat(v), re.I),
            re.compile(M.word_pat(c), re.I) if re.search(M.word_pat(v), c, re.I) else None,
            v)
           for v, c in VARIANTS]
_ENNUM_WS = M.WordSet(list(M.EN_NUM))
_MW_RX = {n: re.compile(M.word_pat(w), re.I) for n, w in M.MW.items()}
# Context-free human rulings applied mechanically BEFORE gating (sieve tier 3):
# data-driven from the termbase's autofix flag — never a hardcoded list.
AUTOFIX = sorted(((v, t["canonical"]) for t in TERMS["terms"] if t.get("autofix")
                  for v in t.get("variants", [])), key=lambda x: -len(x[0]))
_AUTOFIX_RX = [(re.compile(M.word_pat(v), re.I), c) for v, c in AUTOFIX]

def apply_autofix(text):
    """Swap ruled variants to canonical in prose only (comments lifted out);
    case-adapted; longest-first so suffixed forms cannot half-match."""
    parts = []; last = 0
    for m in re.finditer(r"<!--.*?-->", text, re.S):
        parts.append(("p", text[last:m.start()])); parts.append(("c", m.group(0))); last = m.end()
    parts.append(("p", text[last:]))
    def seg_fix(seg):
        for rx, c in _AUTOFIX_RX:
            def rep(mm):
                srcw = mm.group(0)
                if srcw.isupper() and len(srcw) > 1:
                    return c.upper()
                return c[0].upper() + c[1:] if srcw[:1].isupper() and c[:1].islower() else c
            seg = rx.sub(rep, seg)
        return seg
    return "".join(seg if kind == "c" else seg_fix(seg) for kind, seg in parts)

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
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise RuntimeError(f"{model}: auth error {e.code} — not retryable")
            last = f"HTTP {e.code}"
        except Exception as e:
            last = str(e)[:120]
        time.sleep(4 * (a + 1))
    raise RuntimeError(f"{model}: {last}")

# ---------------- block model ----------------
def split_blocks(md):
    """Blank-line blocks; fenced code becomes single PROTECTED blocks; a block that
    is ONLY html comments is protected. A comment directly above prose is NOT —
    protecting those once shipped 799 words of English silently."""
    out, buf, infence, incomment = [], [], False, False
    for line in md.splitlines():
        # Multi-line html comments (DIAGRAM/SOURCE specs) are PROTECTED whole:
        # the single-line CMT model classified them as text, sent machine-read
        # id:/type: fields to the draft model, and — because det_reasons strips
        # comments from both sides — no gate could see the damage (review find).
        if not infence and not incomment and line.lstrip().startswith("<!--") and "-->" not in line:
            if buf:
                out.append(("text", "\n".join(buf))); buf = []
            buf.append(line); incomment = True
            continue
        if incomment:
            buf.append(line)
            if "-->" in line:
                out.append(("prot", "\n".join(buf))); buf = []; incomment = False
            continue
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
        if infence or incomment:
            print(f"WARNING: unterminated {'fence' if infence else 'comment'} — "
                  f"{len(buf)} trailing line(s) protected UNTRANSLATED; fix the source",
                  file=sys.stderr)
        out.append(("prot" if (infence or incomment) else "text", "\n".join(buf)))
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
    if sum(1 for l in out if CMT.match(l)) < len(keep):   # not assert: survives -O,
        raise RuntimeError("comment lost in restore")      # tolerates model-added comment lines
    return "\n".join(out)

def numbered(chunks):
    # source text containing literal [[n]] could displace another block's
    # translation (prompt-injection surface) — break the pattern invisibly
    chunks = [c.replace("[[", "[\u200b[") for c in chunks]
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
_DP_CACHE = {}

def draft_prompt():
    _key = os.environ.get("EXEMPLARS", "43")
    if _key in _DP_CACHE:
        return _DP_CACHE[_key]
    trm = "\n".join(f'- "{t["en"]}" -> {t["canonical"]}  (never: {", ".join(t.get("variants", []))})'
                    for t in TERMS["terms"])
    n = int(os.environ.get("EXEMPLARS", "43"))
    real = [i for i in TESTSET if i["truth"] == "REAL"][:n]
    exs = "\n".join("- " + re.sub(r"^\[[a-z]+\] ", "", e["label"]) for e in real) or "(none supplied)"
    _p = f"""You are a native Malaysian author writing the Bahasa Melayu (Malaysia) edition of a professional book about AI for TVET instructors — not a literal translator. Standard Bahasa Melayu Malaysia, formal but natural; never Indonesian forms.

RULES
- Address the reader as "anda"; instructional we = "kita".
- No comma before "dan" in a series. Use "ialah" before nouns, never "Ini adalah".
- "tool" = "alat" (never "peranti"). "new" = "baharu". Spell numbers 0-9 as words except versions/steps/measurements.
- Where the English verb is "integrate/embed", use "mengintegrasikan" (not "menggunakan"); where English says "adoption" of a technology, prefer "penerimagunaan"; where English says "discipline" (academic), use "disiplin". Plain "use/field/trade" keep menggunakan/bidang. (Site-audit-backed; drafting guidance, not blind replacement.)
- Keep in English, verbatim: {", ".join(_DNT["tokens"])} — and every series/product title: {", ".join(_DNT["phrases"])}. Framework letters keep their English word with a Malay gloss in parentheses on first use.
- A NAMED framework is "Kerangka <NAME>" ("Kerangka GUARD"), never "Kerangka Dasar <NAME>"; "kerangka dasar" is for unnamed policy frameworks only.
- TERMS (binding):
{trm}

EDITOR PRECEDENT — a Malaysian reviewer corrected an earlier translation of this book series; write in the register these corrections point to:
{exs}

TASK: translate each numbered block below into Malay. Return the SAME numbered blocks [[n]] in the same order, nothing else. Preserve markdown (#, **, lists) exactly. Translate heading text. Do not add or drop sentences; every fact, number and name must survive exactly."""
    _DP_CACHE[_key] = _p
    return _p

RW = ("Anda ialah editor buku profesional dari Malaysia. Baiki setiap perenggan bernombor di bawah supaya berbunyi "
      "seperti tulisan asal penulis Malaysia — Bahasa Melayu Malaysia yang baku, formal tetapi lancar. "
      "Betulkan terjemahan harfiah dan susunan ayat yang berbau Inggeris. JANGAN ubah maksud, fakta, angka, nama, "
      "istilah Inggeris yang dikekalkan (AI, TVET, PRISM, TRUST, BENCH, HANDS, GUARD, nama produk dan institusi), "
      "atau struktur markdown (#, **, senarai). Jika sesuatu perenggan sudah baik, kembalikannya tanpa perubahan. "
      "Kembalikan blok bernombor [[n]] yang sama sahaja.")

# ---------------- deterministic checks (mechanics via msml) ----------------
nums, has_word, MW, EN_NUM = M.nums, M.has_word, M.MW, M.EN_NUM

def missing_facts(en_b, t, _ne=None, _nt=None):
    """EN numbers absent from t, excusing 0-9 spelled out, % as 'peratus', X.5 as 'setengah'."""
    ne = _ne if _ne is not None else collections.Counter(nums(en_b))
    nt = _nt if _nt is not None else collections.Counter(nums(t))
    out = []
    spelled_used = collections.Counter()
    for n in (ne - nt).elements():
        if n in MW:
            # count-aware: one 'tiga' excuses one missing '3', not every one
            if spelled_used[n] < len(_MW_RX[n].findall(t)):
                spelled_used[n] += 1
                continue
        if n.endswith("%") and re.search(rf"(?<![\d.,]){re.escape(n[:-1])}\s*(?:%|peratus\b)", t, re.I):
            # left digit boundary: '15 peratus' must not excuse a dropped '5%'
            continue
        if re.fullmatch(r"\d+\.5", n) and has_word(t, "setengah"):
            continue
        out.append(n)
    return out

def invented_facts(en_b, t, _ne=None, _nt=None):
    """Numbers in t whose VALUE the English never contained (bidirectional fact gate)."""
    en_vals = set(_ne if _ne is not None else nums(en_b))
    for w in _ENNUM_WS.present(en_b):
        en_vals.add(EN_NUM[w])
    en_vals |= {v.rstrip("%") for v in en_vals}
    tv = set(_nt if _nt is not None else nums(t))
    return [n for n in tv if n not in en_vals and n.rstrip("%") not in en_vals]

def det_reasons(en_b, cand):
    """Rule violations of one candidate measured against the ENGLISH + the verified
    rules layer. Never measured against a sibling draft.
    Comments are stripped from BOTH sides first: INDEX/DIAGRAM comments carry
    English terms and ids verbatim, and checking them produced phantom residuals
    ('prompt' in INDEX comments, diagram ids as missing facts)."""
    en_b = M.mask_body(en_b)
    cand = M.mask_body(cand)
    reasons = []
    ne = collections.Counter(nums(en_b)); nc = collections.Counter(nums(cand))
    m = missing_facts(en_b, cand, _ne=ne, _nt=nc)
    if m: reasons.append(f"facts missing: {m[:4]}")
    inv = invented_facts(en_b, cand, _ne=ne, _nt=nc)
    if inv: reasons.append(f"facts invented: {inv[:4]}")
    enc, cnc = _DNT_WS.independent_counts(en_b), _DNT_WS.independent_counts(cand)
    lost = [d for d in DNT if enc[d] > cnc[d]]
    if lost: reasons.append(f"DNT lost: {lost}")
    hard = sorted(w for w, n in _ENF_WS.independent_counts(cand).items() if n)
    if hard: reasons.append(f"enforce-tier violation: {hard[:4]}")
    tv = []
    for rx_v, rx_c, v in _VAR_RX:
        n = len(rx_v.findall(cand))
        if n and rx_c is not None:
            # a variant that is a substring of its own canonical phrase must not
            # fire on the canonical ('dwi AI' inside 'kecekapan dwi AI')
            n -= len(rx_c.findall(cand))
        if n > 0:
            tv.append(v)
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
                verdict = d.get("verdict") if isinstance(d, dict) else None
                if isinstance(verdict, str):
                    return verdict.upper() == "PASS", str(d.get("reason", ""))
            except Exception:
                pass   # any malformed shape (null verdict, list, bad JSON) -> retry/fallback
        time.sleep(2)
    return False, "unparseable gate response (2 tries)"

# ---------------- stages (parallel within each stage) ----------------
# Shape-anchored: bare "ialah idiom" deleted legitimate prose sentences (review
# find) — only OUR note formats match now: the (blok N) prefix or the heading.
NOTE_SIG = re.compile(r"^\s*\(blo\w*\s+\d+\)|^\s*IDIOM NOTES\b|^\s*TERM NOTES\b|Jangan calque '", re.I)

def scrub_notes(text):
    """Models sometimes translate the idiom guidance into Malay and emit it as
    content (live bug, ch02 — twice, two different leak paths). The note text is
    ours, so its signature is scrubbable deterministically."""
    return "\n".join(l for l in text.split("\n") if not NOTE_SIG.search(l)).strip()

_IDIOM_WS = M.WordSet([it["phrase"] for it in IDIOMS])
_IDIOM_MAP = {it["phrase"].lower(): it for it in IDIOMS}
def _gloss_aliases(en):
    """Matchable aliases: strip parentheticals, split slashed variants.
    'preventive maintenance (PM)' must match 'preventive maintenance' in prose."""
    base = re.sub(r"\s*\([^)]*\)", "", en)
    return [a.strip().lower() for a in base.split("/") if len(a.strip()) > 3]

_GLOSS_MAP = {}
for _g in GLOSSARY:
    if _g["status"] == "open":
        continue
    for _a in _gloss_aliases(_g["en"]):
        _GLOSS_MAP.setdefault(_a, _g)
_GLOSS_WS = M.WordSet(list(_GLOSS_MAP))

def glossary_notes(chunk):
    """Domain-term guidance, same shape as idiom notes: fires only where a term
    occurs, so 231 entries never bloat the always-on prompt."""
    notes = []
    for j, c in enumerate(chunk):
        for en in sorted(_GLOSS_WS.present(c.lower())):
            g = _GLOSS_MAP[en]
            if g["status"] == "istilah-decided":
                notes.append(f'(block {j+1}) "{g["en"]}" -> use the official istilah "{g["ms"]}" ({g["field"][:30]})')
            elif g["status"] == "proposed":
                notes.append(f'(block {j+1}) "{g["en"]}" -> use "{g["ms"]}" (house rendering; no official istilah). '
                             f'Keep it consistent.')
            else:
                notes.append(f'(block {j+1}) "{g["en"]}": no exact istilah — adjacent: "{g["ms"]}". '
                             f'Choose a natural Malaysian rendering and keep it CONSISTENT across the book.')
    if not notes:
        return ""
    return ("\n\nTERM NOTES — apply these; do not echo them:\n" + "\n".join(notes[:10]))

def idiom_notes(chunk):
    """Per-batch calque prevention: name each English idiom present and how to
    render its MEANING. Fires only where an idiom actually occurs."""
    notes = []
    for j, c in enumerate(chunk):
        for ph in sorted(_IDIOM_WS.present(c.lower())):
            it = _IDIOM_MAP[ph]
            g = it.get("gloss")
            notes.append(f'(block {j+1}) "{it["phrase"]}" is an idiom'
                         + (f" ({g})" if g else "") + f'. {it["ms_guidance"]}')
    if not notes:
        return ""
    return ("\n\nIDIOM NOTES — render the meaning, never the words; do not echo these notes:\n"
            + "\n".join(notes[:12]))

# Diagram-spec comments: the skeleton (id:/type:/field names) stays byte-exact,
# but title:/description: VALUES are reader-facing — the renderer draws every
# diagram label from them. Field-level extraction translates only the values.
DIAG_FIELD = re.compile(r"^(\s*(?:title|description)\s*:\s*)(.+?)(\s*(?:-->)?\s*)$", re.I)
_DIAG_KEY = re.compile(r"^\s*(?:id|type|title|description)\s*:", re.I)

def diagram_fields(blocks):
    """[(block_idx, [line_span], prefix, value, suffix)] for protected diagram
    comments. Wrapped values absorb their continuation lines; a bare terminator
    is never mistaken for a value."""
    out = []
    for bi, (k, b) in enumerate(blocks):
        if k != "prot" or not b.lstrip().startswith("<!--") or "```" in b:
            continue
        lines = b.split("\n")
        i = 0
        while i < len(lines):
            m = DIAG_FIELD.match(lines[i])
            if m and m.group(2).strip() and m.group(2).strip() != "-->":
                span = [i]; val = [m.group(2)]; term_keep = None
                j = i + 1
                while j < len(lines):
                    L = lines[j]
                    if _DIAG_KEY.match(L) or not L.strip():
                        break
                    if "-->" in L:
                        pre2, sep, post = L.partition("-->")
                        if pre2.strip():   # wrapped value sharing the terminator line
                            val.append(pre2.strip()); span.append(j)
                            term_keep = (j, sep + post)
                        break
                    val.append(L.strip()); span.append(j); j += 1
                out.append((bi, span, m.group(1), " ".join(val), m.group(3), term_keep))
                i = j
            else:
                i += 1
    return out

def do_draft(model, blocks, log):
    src = [b for k, b in blocks if k == "text"]
    stripped = [strip_comments(b) for b in src]
    texts = [pr for pr, _ in stripped]
    dfields = diagram_fields(blocks)
    texts = texts + [f[3] for f in dfields]   # field values ride the same protocol
    B = 8
    batches = [(i, texts[i:i + B]) for i in range(0, len(texts), B)]

    def one_batch(arg):
        i, chunk = arg
        got = parse_numbered(call(model, draft_prompt() + idiom_notes(chunk) + glossary_notes(chunk)
                                  + "\n\n" + numbered(chunk),
                                  temp=0.3, stage="draft"), len(chunk))
        out = []
        for j, g in enumerate(got):
            # scrub BEFORE the emptiness check: a note-only response used to pass
            # validation and then scrub to "", shipping a hole where a paragraph was
            g = scrub_notes(g) if g else g
            for _ in range(3):
                if g is not None and g.strip():
                    break
                g = parse_numbered(call(model, draft_prompt() + "\n\n" + numbered([chunk[j]]),
                                         temp=0.3, stage="draft"), 1)[0]
                g = scrub_notes(g) if g else g
            if not (g and g.strip()):
                # Falling back to English is how 799 words once shipped untranslated.
                raise RuntimeError(f"draft failed for block {i + j} after retries; refusing to emit English")
            out.append(g)
        log(f"draft batch {i // B + 1}/{len(batches)} done")
        return i, out

    results = {}
    with concurrent.futures.ThreadPoolExecutor(CONCURRENCY) as ex:
        for i, out in ex.map(one_batch, batches):
            results[i] = out
    flat = [t for i in sorted(results) for t in results[i]]
    nsrc = len(stripped)
    body = [restore_comments(t, keep) for t, (_, keep) in zip(flat[:nsrc], stripped)]
    # Reinsert translated diagram field values — GATED. Review-2 find: values live
    # inside comments, which det_reasons strips, so a bad translation here used to
    # bypass every check. Each value now passes autofix + det_reasons against its
    # own source; failures keep the source value and are REPORTED via
    # LAST_DIAGRAM_ISSUES (run() folds them into residual_rule_issues).
    diagram_issues = []
    blocks = [list(x) for x in blocks]
    by_block = collections.defaultdict(list)
    for field, tr in zip(dfields, flat[nsrc:]):
        by_block[field[0]].append((field, tr))
    for bi, flist in by_block.items():
        lines = blocks[bi][1].split("\n")
        # LAST field first: span deletion must never shift an unprocessed field's
        # indices (review-confirmed corruption in multi-field blocks)
        for (bi2, span, pre, v, suf, term_keep), tr in sorted(flist, key=lambda x: -x[0][1][0]):
            tr = apply_autofix(tr.replace("-->", " ").replace("\n", " ").strip())
            reasons = det_reasons(v, tr)
            if reasons:
                diagram_issues.append({"block": bi, "field_src": v[:80], "why": reasons})
                tr = v      # fail toward the source label, never toward silence
            lines[span[0]] = pre + tr + suf
            for idx in sorted(span[1:], reverse=True):
                if term_keep and idx == term_keep[0]:
                    lines[idx] = term_keep[1]   # the --> terminator survives
                else:
                    del lines[idx]
        blocks[bi][1] = "\n".join(lines)
    it = iter(body)
    return [(k, b) if k == "prot" else ("text", next(it)) for k, b in blocks], diagram_issues

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
    # sieve tier 3 before tier 4: both candidates receive ruled fixes first, so
    # the meaning gate judges repaired text and residuals reflect the final state
    dr_blocks = [(k, apply_autofix(b) if k == "text" else b) for k, b in dr_blocks]
    rw_blocks = [(k, apply_autofix(b) if k == "text" else b) for k, b in rw_blocks]
    for i, ((k, e), (_, d), (_, r)) in enumerate(zip(en_blocks, dr_blocks, rw_blocks)):
        if k == "prot" or r.strip() == d.strip():
            entries[i] = {"i": i, "kind": k, "en": e, "draft": d, "rewrite": None,
                          "final": d, "gate": "unchanged"}
            continue
        dd, rd = det_reasons(e, d), det_reasons(e, r)
        if len(rd) > len(dd):
            entries[i] = {"i": i, "kind": k, "en": e, "draft": d, "rewrite": r, "final": d,
                          "gate": "revert-rules", "final_det": dd,
                          "why": f"draft={dd or 'clean'} rewrite={rd}"}
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
                   "final_det": rd if ok else dd, "why": why}, repaired

    repaired_n = 0
    with concurrent.futures.ThreadPoolExecutor(CONCURRENCY) as ex:
        for i, entry, rep in ex.map(one, need_llm):
            entries[i] = entry
            repaired_n += rep
    log(f"gate: {len(need_llm)} meaning checks done")
    if _VL:
        recs = [{"label": x["gate"], "input": {"en": x["en"][:400], "draft": (x["draft"] or "")[:400],
                 "rewrite": (x["rewrite"] or "")[:400]}, "meta": {"why": x.get("why", "")[:200], "block": x["i"]}}
                for x in entries if x and x["gate"] != "unchanged"]
        _VL.log_verdicts("gate-decisions", recs, source="pipeline")
    final = [(x["kind"], x["final"]) for x in entries]
    return final, entries, repaired_n

atomic_write = M.atomic_write

def run(en_path, outdir, config, name):
    cfg = CFG[config]
    USAGE.clear()   # per-chapter accounting even when embedded in one process
    out = pathlib.Path(outdir); out.mkdir(parents=True, exist_ok=True)
    log = lambda m: print(f"    [{name}] {m}", flush=True)
    t0 = time.time()
    en_blocks = split_blocks(pathlib.Path(en_path).read_text(encoding="utf8"))
    log(f"{len(en_blocks)} blocks ({sum(1 for k, _ in en_blocks if k == 'text')} translatable), concurrency={CONCURRENCY}")
    dr, diagram_issues = do_draft(cfg["draft"], en_blocks, log)
    atomic_write(out / f"{name}-draft.md", join_blocks(dr))
    rw = do_rewrite(cfg["rewrite"], dr, log)
    atomic_write(out / f"{name}-rewrite.md", join_blocks(rw))
    final, entries, repaired = gate(cfg, en_blocks, dr, rw, log)
    atomic_write(out / f"{name}-final.md", join_blocks(final))
    # alignment persisted: regating/judging later reads this, never re-splits from disk
    atomic_write(out / f"{name}-blocks.json", json.dumps(entries, ensure_ascii=False, indent=1))
    changed = [x for x in entries if x["gate"] != "unchanged"]
    kept = sum(1 for x in changed if x["gate"] == "kept")
    residual = [{"i": x["i"], "issues": (x["final_det"] if "final_det" in x
                                         else det_reasons(x["en"], x["final"]))}
                for x in entries if x["kind"] == "text"]
    residual = [r for r in residual if r["issues"]]
    residual += [{"i": d["block"], "issues": [f"diagram field kept in source language: {d['why']}"]}
                 for d in diagram_issues]
    usage = {k: v for k, v in USAGE.items() if k != "est_cost_musd"}
    rep = {"name": name, "config": config, "models": cfg,
           "blocks": len(en_blocks), "changed": len(changed), "kept": kept,
           "reverted": len(changed) - kept, "repaired": repaired,
           "residual_rule_issues": residual, "seconds": round(time.time() - t0),
           "usage": usage, "est_cost_usd": round(USAGE["est_cost_musd"] / 1e6, 4)}
    atomic_write(out / f"{name}-report.json", json.dumps(rep, ensure_ascii=False, indent=1))
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
    name = safe_name(a.name or pathlib.Path(a.en_file).stem)
    run(a.en_file, a.out, a.config, name)

if __name__ == "__main__":
    main()
