#!/usr/bin/env python3
"""Shared text mechanics for the ms-MY pipeline and rules tooling.

Single source of truth. Every scorer bug found in review week was a
duplicated-regex bug (fence tags counted as prose, time formats fixed in one
script but not its siblings, boundaries applied in some copies only). Any
script that masks, counts words, or extracts numbers imports THIS module.
"""
import re

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

NUM = re.compile(r"\d[\d.,%]*")

def nums(s):
    """Number multiset. Times normalize across national conventions first:
    EN '9:00' and Malaysian '9.00' are the same fact."""
    s = re.sub(r"\b(\d{1,2}):(\d{2})\b", r"\1.\2", s)
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
