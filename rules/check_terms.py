#!/usr/bin/env python3
"""Tiered term scan of one translated file: [enforce] = errors, [flag] = advisory,
[collocations], [terms]. One grouped scan per tier (was 1,100+ per-entry scans)."""
import json, sys, os, pathlib, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import msml

HERE = pathlib.Path(__file__).resolve().parent


def main():
    bl = json.load(open(HERE / "ms-indonesian-blocklist.json"))
    tm = json.load(open(HERE / "ms-terms.json"))
    body = msml.mask_file(sys.argv[1])
    print(f"  {pathlib.Path(sys.argv[1]).name}  ({len(body.split()):,} words)\n")
    for tier in ("enforce", "flag"):
        entries = bl.get(tier)
        if not isinstance(entries, list):
            continue
        by_id = {e["avoid_id"].lower(): e for e in entries}
        counts = msml.WordSet(list(by_id)).independent_counts(body)
        counts = type(counts)({k: n for k, n in counts.items() if n})
        print(f"  [{tier}] {len(counts)} distinct Indonesian form(s) present")
        for w, n in counts.most_common(8):
            print(f"      x{n:<3} '{w}' -> should be '{by_id[w]['ms']}'")
    try:
        col = json.load(open(HERE / "ms-collocations.json"))["collocations"]
        cmap = {v.lower(): c["canonical"] for c in col if c.get("status") == "enforced"
                for v in c.get("variants", [])}
        ccounts = msml.WordSet(list(cmap)).independent_counts(body)
        ccounts = type(ccounts)({k: n for k, n in ccounts.items() if n})
        print(f"\n  [collocations] {len(ccounts)} enforced-collocation variant(s) present")
        for v, n in ccounts.items():
            print(f"      x{n:<3} '{v}' -> '{cmap[v]}'")
    except FileNotFoundError:
        pass
    vmap = {v.lower(): t for t in tm["terms"] for v in t.get("variants", [])}
    vcounts = msml.WordSet(list(vmap)).independent_counts(body)
    vcounts = type(vcounts)({k: n for k, n in vcounts.items() if n})
    print("\n  [terms] variants present that should be the canonical form:")
    for v, n in vcounts.items():
        t = vmap[v]
        print(f"      x{n:<3} '{v}' -> '{t['canonical']}'   ({t['en']})")


if __name__ == "__main__":
    main()
