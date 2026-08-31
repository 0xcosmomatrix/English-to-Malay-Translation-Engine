import json, glob, re, sys, collections
S = "/private/tmp/claude-501/-Users-jphq-Straits-Institute-for-Applied-AI/4d01fcbd-2503-4959-8a4d-c7ab333d3dcc/scratchpad"
src = open(f"{S}/flores/flores200_dataset/devtest/eng_Latn.devtest").read().splitlines()
ref = open(f"{S}/flores/flores200_dataset/devtest/zsm_Latn.devtest").read().splitlines()

systems = {}
# engine: stitch part blocks.json in order
eng = []
for f in sorted(glob.glob(f"{S}/flores/out/part*-blocks.json")):
    for b in json.load(open(f)):
        eng.append(re.sub(r"\s+", " ", (b.get("final") or b["en"])).strip())
if len(eng) == len(src): systems["engine (budget)"] = eng
drafts = []
for f in sorted(glob.glob(f"{S}/flores/out/part*-blocks.json")):
    for b in json.load(open(f)):
        drafts.append(re.sub(r"\s+", " ", (b.get("draft") or b["en"])).strip())
if len(drafts) == len(src): systems["engine-draft (pre-rewrite)"] = drafts
else: print(f"WARN engine blocks {len(eng)} != {len(src)}; skipping engine")
for tag, fn in [("qwen-plain", "qwen-plain.ms"), ("sonnet-plain", "sonnet-plain.ms")]:
    try:
        lines = open(f"{S}/flores/out/{fn}").read().splitlines()
        if len(lines) == len(src): systems[tag] = lines
        else: print(f"WARN {tag} {len(lines)} lines")
    except FileNotFoundError: print(f"WARN {tag} missing")

import sacrebleu
rows = {}
for tag, hyp in systems.items():
    chrf = sacrebleu.corpus_chrf(hyp, [ref], word_order=2).score
    bleu = sacrebleu.corpus_bleu(hyp, [ref]).score
    rows[tag] = {"chrf++": round(chrf, 2), "bleu": round(bleu, 2)}

# ms-MY register lens: Indonesian enforce hits + lexicon OOV per system
sys.path.insert(0, f"{S}/engine/rules")
import check_lexicon as CL
known, invalid = CL.lexicon()
bl = json.load(open(f"{S}/engine/rules/ms-indonesian-blocklist.json"))
id_words = {e["avoid_id"].lower() for e in bl["enforce"] if " " not in e["avoid_id"]}
W = re.compile(r"[a-zA-Z][a-zA-Z'-]*")
for tag, hyp in systems.items():
    idn = oov = tot = 0
    for line in hyp:
        for w in W.findall(line):
            lw = w.lower(); tot += 1
            if lw in id_words or lw in invalid: idn += 1
            elif lw not in known: oov += 1
    rows[tag]["indo/invalid per 10k"] = round(1e4 * idn / max(tot, 1), 1)
    rows[tag]["oov %"] = round(100 * oov / max(tot, 1), 2)

json.dump({"systems": {t: h for t, h in systems.items()}, "scores": rows},
          open(f"{S}/flores/scores-partial.json", "w"), ensure_ascii=False)
print(json.dumps(rows, indent=1))

if "--comet" in sys.argv:
    from comet import download_model, load_from_checkpoint
    m = load_from_checkpoint(download_model("Unbabel/wmt22-comet-da"))
    for tag, hyp in systems.items():
        data = [{"src": s, "mt": h, "ref": r} for s, h, r in zip(src, hyp, ref)]
        out = m.predict(data, batch_size=16, gpus=0, num_workers=2, progress_bar=False)
        rows[tag]["comet"] = round(out.system_score * 100, 2)
        print(tag, "comet", rows[tag]["comet"])
    json.dump(rows, open(f"{S}/flores/scores.json", "w"), indent=1)
    print(json.dumps(rows, indent=1))
