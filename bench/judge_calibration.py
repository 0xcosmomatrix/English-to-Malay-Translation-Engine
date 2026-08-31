"""Leg C: seeded-error calibration of panel judges. Plant known defects in clean
segments; a judge's catch rate on defects it should see = its credibility weight."""
import glob, json, os, random, re, urllib.request
from concurrent.futures import ThreadPoolExecutor
S = "/private/tmp/claude-501/-Users-jphq-Straits-Institute-for-Applied-AI/4d01fcbd-2503-4959-8a4d-c7ab333d3dcc/scratchpad"
KEY = os.environ["OPENROUTER_API_KEY"]
rng = random.Random(43)

# clean (en, ms) pairs from the engine A/B blocks
pairs = []
for f in sorted(glob.glob(f"{S}/abtest/engine/*-blocks.json")):
    for b in json.load(open(f)):
        en, ms = b["en"], b.get("final") or ""
        if b["kind"] == "text" and 220 < len(ms) < 600 and "```" not in en and "<!--" not in en and not en.startswith("#"):
            pairs.append((re.sub(r"\s+", " ", en).strip(), re.sub(r"\s+", " ", ms).strip()))
rng.shuffle(pairs)

ID_SWAPS = [("kerana", "karena"), ("boleh", "bisa"), ("syarikat", "perusahaan"), ("maklumat", "informasi"),
            ("jadual", "jadwal"), ("wang", "uang"), ("pejabat", "kantor"), ("kualiti", "kualitas"),
            ("aktiviti", "aktivitas"), ("universiti", "universitas"), ("percuma", "gratis"), ("bandar", "kota")]
def corrupt_number(ms):
    m = re.search(r"\d", ms)
    if not m: return None
    d = m.group(); nd = str((int(d) + 3) % 10)
    return ms[:m.start()] + nd + ms[m.end():], "number-swap"
def corrupt_negation(ms):
    m = re.search(r"\btidak\b", ms)
    if m: return ms[:m.start()] + ms[m.end():].lstrip(), "negation-drop"
    m = re.search(r"\b(boleh|dapat|akan|perlu)\b", ms)
    if m: return ms[:m.start()] + "tidak " + ms[m.start():], "negation-insert"
    return None
def corrupt_clause(ms):
    parts = ms.rsplit(", ", 1)
    if len(parts) == 2 and len(parts[1]) > 25:
        return parts[0] + ".", "clause-drop"
    return None
def corrupt_indo(ms):
    low = ms.lower()
    for good, bad in ID_SWAPS:
        m = re.search(r"\b" + good + r"\b", low)
        if m: return ms[:m.start()] + bad + ms[m.end():], f"indonesian:{bad}"
    return None

CLASSES = [corrupt_number, corrupt_negation, corrupt_clause, corrupt_indo]
seeded, used = [], set()
for fn in CLASSES:
    n = 0
    for en, ms in pairs:
        if n >= 6 or en in used: continue
        out = fn(ms)
        if out and out[0] != ms:
            seeded.append({"en": en, "clean": ms, "bad": out[0], "class": out[1]}); used.add(en); n += 1
print("seeded:", len(seeded), "classes:", sorted({s["class"].split(":")[0] for s in seeded}))
json.dump(seeded, open(f"{S}/abtest/seeded.json", "w"), ensure_ascii=False, indent=1)

JUDGES = {"gpt-5-mini": "openai/gpt-5-mini", "glm-5.3": "z-ai/glm-5.3", "deepseek-v4": "deepseek/deepseek-v4-pro-0813"}
PROMPT = """You are a professional English-to-Malay (Malaysia) translator judging two published translations of the same passage. Judge accuracy to the English, natural Bahasa Melayu Malaysia register (no Indonesian forms), terminology, and completeness.

ENGLISH:
{en}

TRANSLATION A:
{a}

TRANSLATION B:
{b}

Return STRICT JSON: {{"choice":"A"|"B"|"TIE","reason":"<one short sentence>"}}"""
def call(model, prompt):
    body = json.dumps({"model": model, "temperature": 0, "reasoning": {"enabled": False},
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    for a in range(3):
        try:
            r = urllib.request.urlopen(urllib.request.Request(
                "https://openrouter.ai/api/v1/chat/completions", body,
                {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}), timeout=240)
            txt = json.load(r)["choices"][0]["message"]["content"]
            m = re.search(r'"choice"\s*:\s*"(A|B|TIE)"', txt)
            if m: return m.group(1)
        except Exception: pass
    return "ERR"
tasks = []
for i, s in enumerate(seeded):
    for jname, jm in JUDGES.items():
        for order in ("cb", "bc"):   # clean-as-A then bad-as-A
            a, b = (s["clean"], s["bad"]) if order == "cb" else (s["bad"], s["clean"])
            tasks.append((i, jname, jm, order, PROMPT.format(en=s["en"], a=a, b=b)))
def do(t):
    i, jname, jm, order, p = t
    return (i, jname, order, call(jm, p))
with ThreadPoolExecutor(8) as ex:
    results = list(ex.map(do, tasks))
catch = {j: {"caught": 0, "tot": 0} for j in JUDGES}
per_class = {}
for i, jname, order, ch in results:
    s = seeded[i]; cls = s["class"].split(":")[0]
    correct = ("A" if order == "cb" else "B")
    catch[jname]["tot"] += 1
    hit = ch == correct
    if hit: catch[jname]["caught"] += 1
    d = per_class.setdefault(cls, {"caught": 0, "tot": 0}); d["tot"] += 1; d["caught"] += hit
json.dump({"judges": catch, "classes": per_class, "raw": results},
          open(f"{S}/abtest/calibration.json", "w"), indent=1)
for j, c in catch.items(): print(f"{j}: catch {c['caught']}/{c['tot']} = {100*c['caught']/max(c['tot'],1):.0f}%")
for cl, c in per_class.items(): print(f"  class {cl}: {c['caught']}/{c['tot']}")
