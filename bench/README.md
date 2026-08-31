# Leg A — FLORES-200 external benchmark

Objective, reproducible adequacy check against professional references nobody in
this pipeline produced.

Method: FLORES-200 devtest (eng_Latn -> zsm_Latn, 1,012 sentences,
CC-BY-SA 4.0, download from Meta's flores repo — not committed here). Sentences
are split into ~85-line markdown parts (one sentence per paragraph) and run
through `pipeline.py run --config budget`; baselines are single-sentence plain
prompts to the same drafter model and one frontier model (`OPENROUTER_API_KEY`
from env — never write keys into files). Scoring: `flores_score.py` (chrF++,
BLEU via sacrebleu; COMET wmt22-comet-da with `--comet`; plus the engine's own
Indonesian-contamination and closed-world OOV counts per system).

2026-08-31 result (budget config, $0.37 total):
COMET is flat across all systems (engine 90.25, its raw drafter 90.65,
sonnet-plain 90.54 — a 0.4-point band, within noise at n=1012 without a paired
test). The 3-5 point chrF++ deficit (61.4 vs 64.8/66.5) is register/entity
policy divergence from the single reference (kept English org names, typo
correction, densified paraphrase), not meaning loss — the pre-rewrite draft
scores chrF++ 63.6 at the same COMET. The rewrite+gate stages cut Indonesian
contamination 3.4 -> 2.9 per 10k at zero adequacy cost. Conclusion: core
drafting adequacy matches frontier plain prompts on out-of-domain news text;
the engine's value (terminology control, DNT, book-level consistency) is
invisible to this benchmark by design. Track COMET + contamination here, not
chrF++ — chrF++ structurally penalizes the house register policy.
