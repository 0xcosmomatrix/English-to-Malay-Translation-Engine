"""Mechanical harness-value-add scorecard: 3 lanes x 10 chapters, zero LLM judges."""
import collections, glob, json, os, re, sys
S = "/private/tmp/claude-501/-Users-jphq-Straits-Institute-for-Applied-AI/4d01fcbd-2503-4959-8a4d-c7ab333d3dcc/scratchpad"
sys.path.insert(0, f"{S}/engine/rules"); sys.path.insert(0, f"{S}/engine/pipeline")
import msml as M
import check_lexicon as CL
os.environ.setdefault("OPENROUTER_API_KEY", "unused")
import pipeline as P     # loads rule WordSets (DNT, glossary, idioms, enforce)

EN_DIR = f"{S}/full-corpus/Generated Chapters/t2-133-tvet-instructors-chapters-v1.0.0"
en_files = sorted(glob.glob(f"{EN_DIR}/1*-ch*.md"))
known, invalid = CL.lexicon()
bl = json.load(open(f"{S}/engine/rules/ms-indonesian-blocklist.json"))
enf_words = {e["avoid_id"].lower() for e in bl["enforce"] if " " not in e["avoid_id"]}
gloss_dec = {en: g for en, g in P._GLOSS_MAP.items() if g["status"] == "istilah-decided"}
calques = []
for it in P.IDIOMS:
    for m in re.findall(r"[Jj]angan terjemah(?:\s+literal)?\s+(?:seperti\s+)?'([^']{4,60})'", it.get("ms_guidance", "")):
        calques.append((it["phrase"].lower(), m.lower()))
EN_STOP = ["the", "and", "with", "that", "this", "your", "from", "will", "have", "are", "not", "for"]
W = re.compile(r"[a-zA-Z][a-zA-Z'-]*")

def load_lane(tag):
    out = {}
    for f in en_files:
        name = os.path.basename(f)
        if tag == "engine":
            cand = glob.glob(f"{S}/abtest/engine/{P.safe_name(name[:-3])}-final.md")
            out[name] = open(cand[0]).read() if cand else None
        else:
            p = f"{S}/abtest/{tag}/{name}"
            out[name] = open(p).read() if os.path.exists(p) else None
    return out

def score(tag):
    lane = load_lane(tag)
    r = collections.Counter(); adh_hit = adh_tot = 0; details = collections.defaultdict(list)
    for f in en_files:
        name = os.path.basename(f)
        en = open(f).read(); ms = lane[name]
        if ms is None: r["missing_chapters"] += 1; continue
        en_m, ms_m = M.mask_body(en), M.mask_body(ms)
        # DNT: phrases (grouped independent counts) + tokens (case-sensitive)
        enc = P._DNT_WS.independent_counts(en_m); msc = P._DNT_WS.independent_counts(ms_m)
        for k, v in enc.items():
            if msc[k] < v: r["dnt_viol"] += v - msc[k]; details["dnt"].append(f"{name}:{k} {v}->{msc[k]}")
        # istilah adherence: decided glossary terms present in EN
        low_en, low_ms = en_m.lower(), ms_m.lower()
        for alias in P._GLOSS_WS.present(low_en):
            g = gloss_dec.get(alias)
            if not g: continue
            adh_tot += 1
            variants = [v.strip() for v in re.split(r"[,/;]", g["ms"]) if len(v.strip()) > 3]
            if any(v in low_ms for v in variants): adh_hit += 1
            else: details["istilah"].append(f"{name}:{alias}->{g['ms']}")
        # facts: numbers EN vs MS
        en_n, ms_n = collections.Counter(M.nums(en_m)), collections.Counter(M.nums(ms_m))
        lost = sum((en_n - ms_n).values()); phantom = sum((ms_n - en_n).values())
        r["facts_lost"] += lost; r["facts_phantom"] += phantom
        if lost: details["facts"].append(f"{name}: lost {dict((en_n-ms_n))}")
        # contamination + lexicon
        toks = [w.lower() for w in W.findall(ms_m)]
        r["ms_words"] += len(toks)
        r["indo"] += sum(1 for w in toks if w in enf_words or w in invalid)
        r["indo_true"] += sum(1 for w in toks if (w in enf_words or w in invalid)
                              and not re.fullmatch(r"https?|title|trigger|vignette|localisation|english|books|suite|docs|folder|onboarding|quick|publishing|straitsai", w))
        r["oov"] += sum(1 for w in toks if w not in known and not (w in enf_words or w in invalid))
        r["en_stop"] += sum(1 for w in toks if w in EN_STOP)
        # calques (idiom present in EN and its known-bad literal in MS)
        for ph, cq in calques:
            if M.count_word(low_en, ph) and cq in low_ms:
                r["calques"] += 1; details["calques"].append(f"{name}:{ph}->'{cq}'")
        # structure: headings + comments
        eh = len(re.findall(r"^#{1,6} ", en, re.M)); mh = len(re.findall(r"^#{1,6} ", ms, re.M))
        ec = len(re.findall(r"<!--", en)); mc = len(re.findall(r"<!--", ms))
        r["head_delta"] += abs(eh - mh); r["cmt_delta"] += abs(ec - mc)
    w = max(r["ms_words"], 1)
    return {"chapters": 10 - r["missing_chapters"],
            "dnt_violations": r["dnt_viol"],
            "istilah_adherence_%": round(100 * adh_hit / max(adh_tot, 1), 1),
            "istilah_sites": adh_tot,
            "facts_lost": r["facts_lost"], "facts_phantom": r["facts_phantom"],
            "indo_per_10k": round(1e4 * r["indo"] / w, 1),
            "banned_vocab_per_10k": round(1e4 * r["indo_true"] / w, 1),
            "oov_%": round(100 * r["oov"] / w, 2),
            "en_stopwords_per_10k": round(1e4 * r["en_stop"] / w, 1),
            "idiom_calques": r["calques"],
            "heading_delta": r["head_delta"], "comment_delta": r["cmt_delta"],
            "_details": {k: v[:12] for k, v in details.items()}}

res = {t: score(t) for t in ["engine", "qwen", "sonnet"]}
json.dump(res, open(f"{S}/abtest/ab-scores.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps({t: {k: v for k, v in r.items() if k != "_details"} for t, r in res.items()}, indent=1))
