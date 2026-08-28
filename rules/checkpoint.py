#!/usr/bin/env python3
"""Checkpoint miner (idea adapted from EigenformAI's prompt-checkpoint).

Mines the labeled verdict log for recurring failure patterns and asks one model
to propose rule candidates. NOTHING is applied: candidates flow through
rulebook.py propose into open_questions, where they wait with occurrence
evidence for a human ruling — the same gate every other intake passes.

Usage: checkpoint.py <corpus.md ...>   (corpus = current book, for evidence counts)
"""
import json, os, re, sys, collections, pathlib, tempfile, urllib.request, subprocess
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "pipeline"))
import verdictlog as V

MODEL = os.environ.get("CHECKPOINT_MODEL", "z-ai/glm-5.1")

def bucket():
    """Deterministic pattern extraction — the LLM sees summaries, never raw logs."""
    b = {"revert_reasons": collections.Counter(), "meaning_fail_snippets": [],
         "panel_loss_reasons": collections.Counter()}
    for r in V.read_verdicts("gate-decisions"):
        if r["label"] in ("revert-rules", "revert-meaning"):
            why = r["meta"].get("why", "")
            b["revert_reasons"][re.sub(r"[\[\d\]']", "", why)[:80]] += 1
            if r["label"] == "revert-meaning" and len(b["meaning_fail_snippets"]) < 25:
                b["meaning_fail_snippets"].append(why[:150])
    for r in V.read_verdicts("panel-votes"):
        meta = r.get("meta", {})
        pair = meta.get("pair", "")   # trial records: label = winning config
        lost = r["label"] == "ship" or (pair and r["label"] not in ("TIE", pair.split("-vs-")[0]))
        if lost and meta.get("reason"):
            b["panel_loss_reasons"][meta["reason"][:100]] += 1
        elif r["label"] == "ship":
            b["panel_loss_reasons"][meta.get("reason", "(no reason recorded)")[:100]] += 1
    return b

def main():
    corpus = [f for f in sys.argv[1:] if os.path.exists(f)]
    if not corpus:
        print("WARNING: no corpus files exist — occurrence evidence will be x0")
    b = bucket()
    top_reverts = "\n".join(f"- x{n} {k}" for k, n in b["revert_reasons"].most_common(15))
    losses = "\n".join(f"- {k}" for k, _ in b["panel_loss_reasons"].most_common(15))
    fails = "\n".join(f"- {s}" for s in b["meaning_fail_snippets"][:15])
    prompt = f"""You are auditing the verdict history of an English->Malay (Malaysia) book translation pipeline to propose terminology/register rule candidates. Below are recurring patterns.

REWRITE REVERT REASONS (deterministic + meaning gate):
{top_reverts}

MEANING-GATE FAILURE NOTES:
{fails}

BLIND-PANEL LOSS REASONS (judges preferred the older edition):
{losses}

Propose at most 8 rule candidates a NATIVE REVIEWER should rule on. Only patterns that recur; skip anything one-off or already obviously handled. Each: {{"en":"<concept>","orig":"<current Malay form>","sug":"<suggested form>","why":"<one line citing the pattern>"}}.
Return STRICT JSON: {{"candidates":[...]}}"""
    key = os.environ["OPENROUTER_API_KEY"]
    body = json.dumps({"model": MODEL, "messages": [{"role": "user", "content": prompt}],
                       "temperature": 0, "reasoning": {"enabled": False}}).encode()
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=240) as f:
        raw = json.load(f)["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", raw, re.S)
    cands = json.loads(m.group(0)).get("candidates", []) if m else []
    print(f"checkpoint: {len(cands)} candidate(s) proposed by {MODEL}")
    for c in cands:
        print(f"  - {c.get('orig','?')} -> {c.get('sug','?')}  ({c.get('why','')[:70]})")
    if not cands:
        return
    fd, tmp = tempfile.mkstemp(suffix=".json", prefix="checkpoint-")
    with os.fdopen(fd, "w") as f:
        json.dump(cands, f, ensure_ascii=False)
    # the same human-gated intake every correction passes — never straight to terms
    subprocess.run([sys.executable, str(HERE / "rulebook.py"), "propose", tmp,
                    *corpus, "--source", "checkpoint"], check=False)

if __name__ == "__main__":
    main()
