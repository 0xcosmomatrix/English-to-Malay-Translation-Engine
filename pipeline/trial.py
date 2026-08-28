#!/usr/bin/env python3
"""Blind config bake-off (idea adapted from EigenformAI's model trials).

Runs one English chapter through N pipeline configs, assigns stable blind labels,
and (optionally) runs the independent judge panel pairwise vs the first config.
Verdicts land in the labeled log. Judges must come from model families OUTSIDE
the generating pipeline.

Usage: trial.py <en-file.md> --configs budget,premium --out DIR [--judge]
"""
import argparse, hashlib, json, os, random, re, sys, pathlib, importlib.util
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import verdictlog as V
spec = importlib.util.spec_from_file_location("pl", os.path.join(HERE, "pipeline.py"))
P = importlib.util.module_from_spec(spec); spec.loader.exec_module(P)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("en_file"); ap.add_argument("--configs", default="budget,premium")
    ap.add_argument("--out", required=True); ap.add_argument("--judge", action="store_true")
    ap.add_argument("--segments", type=int, default=8)
    a = ap.parse_args()
    cfgs = a.configs.split(",")
    out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
    seed = int(hashlib.md5(a.en_file.encode()).hexdigest()[:8], 16)  # stable across runs
    rng = random.Random(seed)
    labels = dict(zip(cfgs, rng.sample([chr(65 + i) for i in range(len(cfgs))], len(cfgs))))
    json.dump(labels, open(out / "blind-key.json", "w"))    # sealed key
    name = pathlib.Path(a.en_file).stem
    for c in cfgs:
        P.USAGE.clear()
        P.run(a.en_file, str(out / labels[c]), c, name)
    print(f"trial complete: versions {sorted(labels.values())} in {out} (key sealed in blind-key.json)")
    if not a.judge:
        return
    # pairwise vs first config, both orders, order-flip = TIE
    base, rest = cfgs[0], cfgs[1:]
    def blocks(c):
        return [e for e in json.load(open(out / labels[c] / f"{name}-blocks.json"))
                if e["kind"] == "text" and len(e["en"].split()) > 30]
    bb = blocks(base)
    import urllib.request, collections, time
    def call(model, p):
        body = json.dumps({"model": model, "messages": [{"role": "user", "content": p}],
                           "temperature": 0, "reasoning": {"enabled": False}}).encode()
        r = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=body,
            headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}", "Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=240) as f:
            return json.load(f)["choices"][0]["message"]["content"] or ""
    JUDGES = ["z-ai/glm-5.1", "anthropic/claude-haiku-4.5"]   # outside the generating pipeline
    for other in rest:
        ob = {e["en"]: e for e in blocks(other)}
        segs = [e for e in bb if e["en"] in ob][:a.segments]
        agg = collections.Counter()
        for e in segs:
            x, y = e["final"], ob[e["en"]]["final"]
            votes = []
            for j in JUDGES:
                def v(p):
                    for _ in range(3):
                        try:
                            m = re.search(r'"choice"\s*:\s*"(A|B|TIE)"', call(j, p))
                            return {"A": "A", "B": "B", "TIE": "T"}[m.group(1)] if m else "T"
                        except Exception:
                            time.sleep(3)
                    return "T"   # transient judge failure = tie, never an aborted trial
                pr = lambda p1, p2: (f"Professional English->Malay (Malaysia) editor: which translation would you print? Note: 'prom' is the official DBP istilah for an AI prompt.\n\nENGLISH:\n{e['en']}\n\nA:\n{p1}\n\nB:\n{p2}\n\n" + 'STRICT JSON: {"choice":"A"|"B"|"TIE"}')
                v1 = {"A": base, "B": other, "T": "TIE"}[v(pr(x, y))]
                v2 = {"A": other, "B": base, "T": "TIE"}[v(pr(y, x))]
                votes.append(v1 if v1 == v2 else "TIE"); time.sleep(1)
            c = collections.Counter(votes)
            win = base if c[base] > c[other] else (other if c[other] > c[base] else "TIE")
            agg[win] += 1
            V.log_verdicts("panel-votes", [{"label": win, "input": {"en": e["en"][:400], "a": x[:400], "b": y[:400]},
                                           "meta": {"trial": str(out), "pair": f"{base}-vs-{other}"}}], "trial")
        print(f"  {base} vs {other}: {dict(agg)}")

if __name__ == "__main__":
    main()
