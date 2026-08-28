# English → Malay Translation Engine

A verification-first pipeline for translating professional books from English to
Bahasa Melayu (Malaysia). One principle, learned empirically: **sort every failure
class by how cheaply it can be verified, and let the cheapest sound layer decide
first and terminally.**

Measured on the AI+ TVET Instructors book (10 chapters, ~26k words):
**9.5 minutes and $0.29 per book** · zero fact defects, zero enforcement
violations, zero untranslated residue · preferred over the previously shipped
human-pipeline edition **38–5–7** by a blind three-family judge panel
(both presentation orders, order-flips scored as ties).

## Architecture — the sieve

```
0  PREP        mask fences/comments/DNT; per-batch idiom notes (calque prevention)
1  DRAFT       LLM, rules in prompt — prompt/gate symmetry; batches run in parallel
2  REWRITE     monolingual Malay editor pass, parallel batches
3  GATES       deterministic first (facts vs SOURCE both directions, DNT tokens+
               phrases case-sensitively, enforce tier, term/collocation variants),
               then diff-scoped LLM meaning checks in parallel; select the better
               of {draft, rewrite} per block, both scored against the ENGLISH
4  REPAIR      mechanical application of context-free human rulings to residual
               sites; every edit verified cleaner-or-rolled-back
5  REPORT      everything else is advisory: ranked flags, lexicon OOV, telemetry
```

Every chapter emits aligned `blocks.json` (EN ↔ draft ↔ rewrite ↔ final), a
report with per-stage token usage and estimated cost, and residual findings.

## The rules layer — the ledger

`rules/` holds the enforcement data, every entry provenance-stamped and the
whole tier CI-validated on each push:

| file | contents |
|---|---|
| `ms-indonesian-blocklist.json` | **enforce: 193** (3-model unanimous panel + PRPM verification + corpus back-test) · **flag: ~877** advisory (Wiktionary spelling appendix, CC BY-SA) |
| `ms-terms.json` | 26 cited term rulings (operator/reviewer/istilah-backed) · look-alike `distinct` records · `open_questions` ruling queue with occurrence counts, contexts, and oracle testimony attached |
| `ms-prpm-ledger.json` | **1,221 cached dictionary rulings** (verdict, ≤10-word evidence, date) — each word consulted once, ever; the ledger **outranks corpus membership** |
| `ms-dnt.json` | Do-Not-Translate registry: tokens + multi-word product/series titles, counted case-sensitively EN-vs-candidate |
| `ms-collocations.json` | enforced + candidate multi-word collocations |
| `en-idioms.json` | 89 corpus-harvested English idioms with render-the-meaning guidance, injected per batch only where a phrase occurs |
| `ms-figurative.json` | 87 Malay idioms/proverbs (Wiktionary) — licence list for validating figurative output, never for generating it |
| `ms-wiktionary-lemmas.json` | 10,553 Malay lemmas (Wiktionary, CC BY-SA) — mid-trust vouching tier |
| `msml.py` | shared text mechanics: masking, word boundaries, number extraction with time/% normalization — the single source of truth |
| `enforce_gate.py` | the **only** sanctioned writer of the enforce tier; validates all three legs; **fails closed** |
| `rulebook.py` | correction intake → evidence → human ruling; oracle-gated promotion |
| `check_terms.py` / `check_lexicon.py` | tiered corpus checker · closed-world OOV screen (a word must be vouched by corpus−ledger-invalid ∪ ledger ∪ lemmas ∪ DNT ∪ source ∪ morphology) |

**Three legs to enforce, none optional:** unanimous 3-family model panel
(calibrated on blind knowns) → PRPM/Kamus Dewan oracle ruling (`VALID_MALAY` is
an unforceable refusal) → zero hits across human-approved corpora. The panel
alone would have shipped 28 bad rules (`kanker`, `kursi` — obscure real senses
only the dictionary knew); the oracle blocked every one. The corpus tier was
itself audited against the oracle (704 words): 132 shipped-but-invalid words
were demoted from vouching power.

## Status

Production-shaped and instrumented, human-in-the-loop by design:

- `tests/run_tests.py`: **17 regression checks, each pinning a bug that actually
  shipped** (comment loss, invented facts, note echo/translation leaks, boundary
  and case collisions, tokenizer artifacts). CI runs them plus the gate and
  compile checks on every push.
- The residual ruling queue is small and genuinely human: register choices and
  coinage confirmations, delivered with occurrence counts, live contexts, and
  dictionary testimony so a native session is adjudication, not proofreading.
- Known ceiling: register/naturalness converges through rulings and sampled
  native review; it is measured, not certified.

## What is deliberately NOT here

- **Book content**: sources, translations, and the approved Malay corpus are
  proprietary. Populate `corpus/private/` locally (see `corpus/README.md`);
  the gate fails closed without it and CI runs `--check --no-corpus`.
- **Microsoft terminology glossary**: excluded on license grounds (approach
  tested and rejected — different localization choices are not errors).

## Quickstart

```
# translate one chapter / a whole book (OPENROUTER_API_KEY required)
python3 pipeline/pipeline.py run chapter.md --out out/ --config budget
python3 pipeline/run_book.py corpus/private/en --out out/ --jobs 3

# apply context-free rulings to residual sites (verified, rollback-on-non-improvement)
python3 pipeline/repair.py out/

# validate the rules layer / scan a translation / OOV screen
python3 rules/enforce_gate.py --check
python3 rules/check_terms.py translated.md
python3 rules/check_lexicon.py translated.md --en source.md

# correction intake -> ruling -> promotion (oracle-gated)
python3 rules/rulebook.py propose corrections.json chapter.md
python3 rules/rulebook.py rule "<term>" --accept
```

Model config (measured): drafter `qwen/qwen3.5-397b-a17b` with
`reasoning: {enabled: false}` — 98% of its default output was billed reasoning
tokens; disabling it was 23× cheaper *and* scored better. Rewriter/gate
`google/gemma-4-26b-a4b-it`. Judges for evaluation panels must come from
families outside the generating pipeline.

## Provenance & attribution

Blocklist flag tier and the lemma inventory derive from English Wiktionary
(CC BY-SA). Dictionary evidence quotes (≤10 words) from PRPM / Kamus Dewan
Edisi Keempat, Dewan Bahasa dan Pustaka — consulted per-word as an authority
with polite rate limits, never bulk-copied.
