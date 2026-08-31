#!/usr/bin/env python3
"""Shared text mechanics for the ms-MY pipeline and rules tooling.

Single source of truth. Every scorer bug found in review week was a
duplicated-regex bug (fence tags counted as prose, time formats fixed in one
script but not its siblings, boundaries applied in some copies only). Any
script that masks, counts words, or extracts numbers imports THIS module.
"""
import collections, json, os, pathlib, re

CMT = re.compile(r"^\s*<!--.*?-->\s*$")

def mask_body(text):
    """Prose only: strip html comments and fenced code (prompts hold verbatim English)."""
    t = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return re.sub(r"```.*?```", "", t, flags=re.S)

def word_pat(s):
    """Word-boundary pattern for single tokens AND multi-word phrases.
    Boundaries on phrases matter: 'rasa tidak yakin' must not match inside
    'berasa tidak yakin'."""
    return rf"(?<![\w-]){re.escape(s)}(?![\w-])"

def has_word(t, w):
    return re.search(word_pat(w), t, re.I) is not None

def count_word(t, w):
    return len(re.findall(word_pat(w), t, re.I))

def count_word_cs(t, w):
    """Case-SENSITIVE count. DNT tokens are proper names: 'TRUST' the framework
    must not collide with 'trust' the verb (live false-positive, 11 blocks)."""
    return len(re.findall(word_pat(w), t))

NUM = re.compile(r"\d[\d.,%]*")

def nums(s):
    """Number multiset. Normalized before extraction: times across national
    conventions (EN 9:00 = MS 9.00) and thousands separators (1,000 = 1000) —
    without this, a correct translation earns BOTH a phantom missing fact and a
    phantom invented one, and cosmetics can decide gate selection."""
    s = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", s)
    s = re.sub(r"\b(\d{1,2}):(\d{2})(?!\d)", r"\1.\2", s)   # also 3:30pm — 'pm' defeats \b
    return sorted(re.sub(r"[.,]+$", "", m) for m in NUM.findall(s))

# 0-9 may be spelled out per the ms-MY style rule
MW = {"0": "sifar", "1": "satu", "2": "dua", "3": "tiga", "4": "empat",
      "5": "lima", "6": "enam", "7": "tujuh", "8": "lapan", "9": "sembilan"}
EN_NUM = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
          "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11",
          "twelve": "12", "thirteen": "13", "fourteen": "14", "fifteen": "15", "sixteen": "16",
          "seventeen": "17", "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30",
          "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80",
          "ninety": "90", "hundred": "100", "thousand": "1000", "million": "1000000"}


def mask_file(path):
    return mask_body(pathlib.Path(path).read_text(encoding="utf8"))

def atomic_write(path, text):
    """Write-temp-then-rename: an exception mid-run must never leave a torn file."""
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf8") as f:
        f.write(text)
    os.replace(tmp, str(path))

def extract_json(raw):
    """Strict-JSON out of an LLM reply: strips markdown fences, tolerates prose
    around the object. Returns a dict or None — never raises."""
    if not raw:
        return None
    s = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M)
    mm = re.search(r"\{.*\}", s, re.S)
    if not mm:
        return None
    try:
        d = json.loads(mm.group(0))
        return d if isinstance(d, dict) else None
    except Exception:
        return None

class WordSet:
    """Precompiled grouped alternation over words/phrases with the house
    boundaries. One scan replaces N per-word scans — the det-gate hot path ran
    ~75k per-call pattern builds per chapter before this existed."""
    def __init__(self, words, flags=re.I):
        self.words = [w for w in words if w]
        self.flags = flags
        if self.words:
            alt = "|".join(re.escape(w) for w in sorted(self.words, key=len, reverse=True))
            self.rx = re.compile(rf"(?<![\w-])(?:{alt})(?![\w-])", flags)
        else:
            self.rx = None
    def counts(self, text):
        c = collections.Counter()
        if self.rx:
            fold = (self.flags & re.I) != 0
            for mm in self.rx.finditer(text):
                c[mm.group(0).lower() if fold else mm.group(0)] += 1
        return c
    def present(self, text):
        return set(self.counts(text))

    def _embeddings(self):
        """(short -> [(long_key, k)]) for boundary-respecting containments, so
        independent_counts can restore per-word arithmetic: longest-first pooled
        scans consume 'uang tunai' whole and 'uang' would otherwise lose its
        embedded occurrences — a review-confirmed verdict flip."""
        if not hasattr(self, "_embed"):
            fold = (self.flags & re.I) != 0
            emb = {}
            for w1 in self.words:
                for w2 in self.words:
                    if w1 is w2 or len(w1) >= len(w2):
                        continue
                    k = len(re.findall(rf"(?<![\w-]){re.escape(w1)}(?![\w-])", w2, self.flags))
                    if k:
                        key1 = w1.lower() if fold else w1
                        key2 = w2.lower() if fold else w2
                        emb.setdefault(key1, []).append((key2, k))
            self._embed = emb
        return self._embed

    def independent_counts(self, text):
        """Counts as if each word were scanned alone (old per-word semantics):
        pooled counts plus embedded occurrences inside longer members."""
        pooled = self.counts(text)
        out = collections.Counter(pooled)
        for short, lst in self._embeddings().items():
            for long_key, k in lst:
                if pooled[long_key]:
                    out[short] += k * pooled[long_key]
        return out
