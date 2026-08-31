"""Head-to-head calibrated panel: engine final vs rival final, EN-anchored segments."""
import glob, json, os, random, re, urllib.request
from concurrent.futures import ThreadPoolExecutor
S = "/private/tmp/claude-501/-Users-jphq-Straits-Institute-for-Applied-AI/4d01fcbd-2503-4959-8a4d-c7ab333d3dcc/scratchpad"
KEY = os.environ["OPENROUTER_API_KEY"]
rng = random.Random(202608)

def paras(txt):
    return [re.sub(r"\s+", " ", p).strip() for p in txt.split("\n\n") if len(p.strip()) > 150 and "```" not in p and "<!--" not in p]
def rare_tokens(s):
    return {w for w in re.findall(r"[A-Za-z]{6,}|\d+", s)}

segs = []
for f in sorted(glob.glob(f"{S}/abtest/engine/*-blocks.json")):
    base = os.path.basename(f).replace("-final.md", "").replace("-blocks.json", "")
    src = [b for b in json.load(open(f)) if b["kind"] == "text" and len(b["en"]) > 250 and "```" not in b["en"] and not b["en"].startswith("#")]
    riv_file = glob.glob(f"{S}/abtest/rival/{base}*.md") or glob.glob(f"{S}/abtest/rival/*{base[4:20]}*.md")
    rf = [x for x in glob.glob(f"{S}/abtest/rival/*.md") if os.path.basename(x).startswith(base.split("-ai-")[0][:12])]
    if not rf: continue
    riv_paras = paras(open(rf[0]).read())
    picked = 0
    rng.shuffle(src)
    for b in src:
        if picked >= 5: break
        anchors = {w for w in re.findall(r"\d+|[A-Z][a-z]{5,}", b["en"])}
        en_low_rare = rare_tokens(b["en"].lower())
        best, bs = None, 0
        for rp in riv_paras:
            score = len(anchors & set(re.findall(r"\d+|[A-Z][a-z]{5,}", rp))) * 3 + len(en_low_rare & rare_tokens(rp.lower())) * 0
            # anchor on shared digits/named tokens; fall back to length-position later
            if score > bs: bs, best = score, rp
        if best and bs >= 3:
            segs.append({"ch": base[:12], "en": re.sub(r"\s+", " ", b["en"]).strip(),
                         "engine": re.sub(r"\s+", " ", b["final"]).strip(), "rival": best})
            picked += 1
print(f"aligned segments: {len(segs)} across {len({s['ch'] for s in segs})} chapters")
json.dump(segs, open(f"{S}/abtest/hth-segments.json", "w"), ensure_ascii=False, indent=1)

JUDGES = {"gpt-5-mini": ("openai/gpt-5-mini", {"reasoning": {"effort": "low"}}),
          "glm-5.3": ("z-ai/glm-5.3", {"temperature": 0}),
          "deepseek-v4": ("deepseek/deepseek-v4-pro-0813", {"temperature": 0, "reasoning": {"enabled": False}})}
PROMPT = """You are a professional English-to-Malay (Malaysia) translator judging two published translations of the same passage. Note: 'prom' is the official DBP istilah for an AI prompt; treat it as correct terminology. Judge as an editor deciding which to print: accuracy to the English, natural Bahasa Melayu Malaysia register (no Indonesian forms, no calques), terminology, completeness, and rhythm.

ENGLISH:
{en}

TRANSLATION A:
{a}

TRANSLATION B:
{b}

Return STRICT JSON: {{"choice":"A"|"B"|"TIE","reason":"<one short sentence>"}}"""
def call(model, extra, prompt):
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}], **extra}).encode()
    for a in range(3):
        try:
            r = urllib.request.urlopen(urllib.request.Request(
                "https://openrouter.ai/api/v1/chat/completions", body,
                {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}), timeout=300)
            txt = json.load(r)["choices"][0]["message"]["content"] or ""
            m = re.findall(r'"choice"\s*:\s*"(A|B|TIE)"', txt)
            if m: return m[-1]
        except Exception: pass
    return "ERR"
tasks = []
for i, s in enumerate(segs):
    for jn, (jm, ex) in JUDGES.items():
        for order in ("er", "re"):
            a, b = (s["engine"], s["rival"]) if order == "er" else (s["rival"], s["engine"])
            tasks.append((i, jn, jm, ex, order, PROMPT.format(en=s["en"], a=a, b=b)))
with ThreadPoolExecutor(10) as ex2:
    raw = list(ex2.map(lambda t: (t[0], t[1], t[4], call(t[2], t[3], t[5])), tasks))
json.dump(raw, open(f"{S}/abtest/hth-raw.json", "w"))
# order-consistent per judge: engine wins only if it wins BOTH orders; flip => TIE
per_judge = {}
seg_votes = {}
for i, jn, order, ch in raw:
    win = "engine" if (ch == "A") == (order == "er") else ("rival" if ch in ("A", "B") else "tie")
    if ch == "TIE": win = "tie"
    seg_votes.setdefault(i, {}).setdefault(jn, []).append(win)
for i, js in seg_votes.items():
    for jn, votes in js.items():
        v = votes[0] if len(set(votes)) == 1 else "tie"
        d = per_judge.setdefault(jn, {"engine": 0, "rival": 0, "tie": 0}); d[v] += 1
print(json.dumps(per_judge, indent=1))
maj = {"engine": 0, "rival": 0, "tie": 0}
for i, js in seg_votes.items():
    tally = {"engine": 0, "rival": 0, "tie": 0}
    for jn, votes in js.items():
        v = votes[0] if len(set(votes)) == 1 else "tie"
        tally[v] += 1
    maj[max(tally, key=tally.get) if max(tally.values()) >= 2 else "tie"] += 1
print("SEGMENT MAJORITY:", maj)
