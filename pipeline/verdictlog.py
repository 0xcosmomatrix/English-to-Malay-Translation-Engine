#!/usr/bin/env python3
"""Labeled-verdict log (idea adapted from EigenformAI's finetune-log).

Every judgment the system makes is a labeled datapoint: meaning-gate PASS/FAIL,
det-gate revert reasons, panel votes, sense-audit choices, dictionary verdicts,
human rulings. Theirs logs one arbiter; this logs every layer, one JSONL per
kind under DATA_DIR (gitignored — records carry book text excerpts).

Record shape: {ts, kind, source, label, input: {...}, meta: {...}}
"""
import json, os, time, pathlib

DATA_DIR = pathlib.Path(os.environ.get("VERDICT_DATA_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "finetune-data")))

def log_verdicts(kind, records, source):
    """Append labeled records; never raises (logging must not break a run)."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(DATA_DIR / f"{kind}.jsonl", "a", encoding="utf8") as f:
            for r in records:
                f.write(json.dumps({"ts": int(time.time()), "kind": kind,
                                    "source": source, **r}, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"    [verdictlog] skipped: {e}")

def read_verdicts(kind):
    p = DATA_DIR / f"{kind}.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in open(p, encoding="utf8") if l.strip()]
