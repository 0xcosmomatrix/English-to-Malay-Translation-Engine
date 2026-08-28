# English → Malay Translation Engine

A verification-first pipeline for translating professional books from English to
Bahasa Melayu (Malaysia). Built on one principle, learned empirically: **sort every
failure class by how cheaply it can be verified, and let the cheapest sound layer
decide first and terminally.**

## Architecture — the sieve

```
0  PREP        mask fences/comments/DNT; annotate blocks with term bindings
1  DRAFT       LLM, rules in prompt (prompt/gate symmetry: gates only check what was asked)
2  HARD GATES  deterministic: enforce tier, facts vs SOURCE, structure — acts, never flags
3  REWRITE     monolingual Malay editor pass; segment-level accept/revert,
               both candidates scored against the ENGLISH (never each other)
4  LLM CHECKS  diff-scoped, binary questions only
5  FLAG REPORT advisory, ranked, suppressed against the approved corpus
```

## The rules layer — the ledger

`rules/` carries the enforcement data, every entry provenance-stamped:

| file | contents |
|---|---|
| `ms-indonesian-blocklist.json` | **enforce tier: 193 entries** (3-model unanimous panel + PRPM/Kamus Dewan verification + corpus back-test) · **flag tier: ~884** advisory entries (Wiktionary spelling-differences appendix, CC BY-SA) |
| `ms-terms.json` | book termbase: enforced canonical forms, look-alike `distinct` records, `open_questions` awaiting human ruling with evidence attached |
| `ms-prpm-ledger.json` | 262 cached dictionary rulings (verdict, ≤10-word evidence quote, date) — each word consulted once, ever |
| `enforce_gate.py` | the **only** sanctioned writer of the enforce tier; validates all three legs; **fails closed** |
| `rulebook.py` | correction intake: propose → frequency/context evidence → human ruling; oracle-gated promotion |
| `check_terms.py` | tiered corpus checker (`[enforce]` = errors, `[flag]` = advisory) |

**Three legs to enforce, none optional:** unanimous 3-family model panel (calibrated
on blind knowns, sub-80% auditors discarded) → PRPM oracle ruling (`VALID_MALAY`
is an unforceable refusal) → zero hits across every human-approved corpus.
The panel alone would have shipped 28 bad rules (`kanker`, `kursi`, `jawaban` —
all have obscure real Malay senses); the oracle blocked every one.

## Status: prototype with verified data

Measured on chapter 1 (independent re-audit reproduced all numbers from disk):
reviewer-flag checks clean 4/13 → **11/13**; all-43 not-broken 51% → **74%**;
fact loss 1 → **0**; cost ~$4.14/book (non-converging) → **~$0.16** (converging,
reasoning tokens disabled — 98% of Qwen output was billed reasoning).

Known issues from the adversarial build review (see `docs/build-review.html`):
three criticals are fixed in this repo (fail-open corpus leg, default-pass panel
leg, registry pointing at temp storage); still open — `restore_comments` line-loss,
`regate.py` misalignment, one-directional fact scoring in `gate_v3`, multi-word
matching in `check43`, and `pipeline.py`'s entrypoint predating the sibling fixes.
Treat `pipeline/` and `eval/scoring/` as lab code; treat `rules/` as data you can rely on.

## What is deliberately NOT here

- **Book content**: source chapters, translations, and the approved Malay corpus
  are proprietary. Populate `corpus/private/` locally (see `corpus/README.md`);
  the gate fails closed without it, and CI runs `--check --no-corpus` (legs 1–2 only).
- **Microsoft terminology glossary**: excluded on license grounds (and its
  cross-reference approach was tested and rejected — different localization
  choices are not errors).

## Quickstart

```
# validate the rules layer (legs 1-2 + provenance; no corpora needed)
python3 rules/enforce_gate.py --check --no-corpus

# full three-leg check (after populating corpus/private/)
python3 rules/enforce_gate.py --check

# scan a translated chapter
python3 rules/check_terms.py path/to/chapter.md

# correction intake -> ruling sheet -> promotion (oracle-gated)
python3 rules/rulebook.py propose corrections.json chapter.md
python3 rules/rulebook.py rule "<term>" --accept
```

Pipeline model config (measured, not assumed): drafter `qwen/qwen3.5-397b-a17b`
with `reasoning: {enabled: false}`; rewriter/gate `google/gemma-4-26b-a4b-it`;
keys via `OPENROUTER_API_KEY`.

## Provenance & attribution

Blocklist flag tier derives from Wiktionary's *Spelling differences between
Indonesian and Standard Malay* appendix (CC BY-SA). Dictionary evidence quotes
(≤10 words) from PRPM / Kamus Dewan Edisi Keempat, Dewan Bahasa dan Pustaka —
consulted per-word as an authority, never bulk-copied.
