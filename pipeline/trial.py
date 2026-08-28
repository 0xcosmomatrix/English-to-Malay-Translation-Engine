#!/usr/bin/env python3
"""Blind config bake-off (idea adapted from EigenformAI's model trials).

Runs one English chapter through N pipeline configs, assigns stable blind labels,
and (optionally) runs the independent judge panel pairwise vs the first config.
Verdicts land in the labeled log. Judges must come from model families OUTSIDE
the generating pipeline.

Usage: trial.py <en-file.md> --configs budget,premium --out DIR [--judge]
"""
import argparse, collections, concurrent.futures, hashlib, json, os, random, re, sys, pathlib
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import verdictlog as V
import pipeline as P

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
    letters = [chr(65 + i) for i in range(len(cfgs))]
    rng.shuffle(letters)
    labels = dict(zip(cfgs, letters))
    P.atomic_write(out / "blind-key.json", json.dumps(labels))    # sealed key
    name = P.safe_name(pathlib.Path(a.en_file).stem)
    for c in cfgs:
        P.run(a.en_file, str(out / labels[c]), c, name)   # run() clears USAGE itself
    print(f"trial complete: versions {sorted(labels.values())} in {out} (key sealed in blind-key.json)")
    if not a.judge:
        return
    # pairwise vs first config, both orders, order-flip = TIE
    base, rest = cfgs[0], cfgs[1:]
    def blocks(c):
        return [e for e in json.load(open(out / labels[c] / f"{name}-blocks.json"))
                if e["kind"] == "text" and len(e["en"].split()) > 30]
    bb = blocks(base)
    JUDGES = ["z-ai/glm-5.1", "anthropic/claude-haiku-4.5"]   # outside the generating pipeline

    def judge_prompt(en, p1, p2):
        return (f"Professional English->Malay (Malaysia) editor: which translation would you print? "
                f"Note: 'prom' is the official DBP istilah for an AI prompt.\n\nENGLISH:\n{en}\n\nA:\n{p1}\n\nB:\n{p2}\n\n"
                + 'STRICT JSON: {"choice":"A"|"B"|"TIE"}')

    def one_vote(judge, en, p1, p2):
        try:
            raw = P.call(judge, judge_prompt(en, p1, p2), temp=0.0, stage="judge", timeout=120)
        except RuntimeError:
            return "T"   # transient judge failure = tie, never an aborted trial
        d = P.M.extract_json(raw) or {}
        return {"A": "A", "B": "B", "TIE": "T"}.get(str(d.get("choice", "")).upper(), "T")
    for other in rest:
        ob = {e["en"]: e for e in blocks(other)}
        segs = [e for e in bb if e["en"] in ob][:a.segments]
        # all (segment, judge, order) calls are independent — run them concurrently
        tasks = [(si, j, o) for si in range(len(segs)) for j in JUDGES for o in (0, 1)]
        def run_task(t):
            si, j, o = t
            e = segs[si]; x, y = e["final"], ob[e["en"]]["final"]
            raw = one_vote(j, e["en"], *( (x, y) if o == 0 else (y, x) ))
            fwd = {"A": base, "B": other, "T": "TIE"} if o == 0 else {"A": other, "B": base, "T": "TIE"}
            return si, j, fwd[raw]
        votes_by = collections.defaultdict(dict)
        with concurrent.futures.ThreadPoolExecutor(P.CONCURRENCY) as ex:
            for si, j, vv in ex.map(run_task, tasks):
                votes_by[si].setdefault(j, []).append(vv)
        agg = collections.Counter()
        for si, e in enumerate(segs):
            x, y = e["final"], ob[e["en"]]["final"]
            votes = [(vs[0] if len(set(vs)) == 1 else "TIE") for vs in votes_by[si].values()]
            c = collections.Counter(votes)
            win = base if c[base] > c[other] else (other if c[other] > c[base] else "TIE")
            agg[win] += 1
            V.log_verdicts("panel-votes", [{"label": win, "input": {"en": e["en"][:400], "a": x[:400], "b": y[:400]},
                                           "meta": {"trial": str(out), "pair": f"{base}-vs-{other}"}}], "trial")
        print(f"  {base} vs {other}: {dict(agg)}")

if __name__ == "__main__":
    main()
