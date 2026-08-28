import json, sys, os as _os
sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import msml,re,sys,pathlib,collections
HERE=pathlib.Path(__file__).resolve().parent
bl=json.load(open(HERE/"ms-indonesian-blocklist.json")); tm=json.load(open(HERE/"ms-terms.json"))
def main():
    text=pathlib.Path(sys.argv[1]).read_text(encoding="utf8")
    # strip code fences and HTML comments — prompts hold verbatim English
    body=msml.mask_body(text)
    def hits(term):
        return msml.count_word(body, term)
    print(f"  {pathlib.Path(sys.argv[1]).name}  ({len(body.split()):,} words)\n")
    # Tiers are whatever the blocklist actually declares. Hardcoding
    # ("enforce","flag","ruling") made this script die on KeyError against the
    # shipped blocklist, which only carries "flag" — so it never ran at all.
    TIERS=[k for k in ("enforce","flag","ruling") if isinstance(bl.get(k),list)]
    if not TIERS: print("  (blocklist declares no known tiers)")
    for tier in TIERS:
        found=collections.Counter()
        for e in bl[tier]:
            n=hits(e["avoid_id"])
            if n: found[(e["avoid_id"],e["ms"])]+=n
        print(f"  [{tier}] {len(found)} distinct Indonesian form(s) present")
        for (a,m),n in found.most_common(8): print(f"      x{n:<3} '{a}' -> should be '{m}'")
    try:
        col=json.load(open(HERE/"ms-collocations.json"))["collocations"]
        found=[(v,c["canonical"]) for c in col if c.get("status")=="enforced" for v in c.get("variants",[]) if hits(v)]
        print(f"\n  [collocations] {len(found)} enforced-collocation variant(s) present")
        for v,canon in found: print(f"      x{hits(v):<3} '{v}' -> '{canon}'")
    except FileNotFoundError: pass
    print("\n  [terms] variants present that should be the canonical form:")
    for t in tm["terms"]:
        for v in t.get("variants",[]):
            n=hits(v)
            if n: print(f"      x{n:<3} '{v}' -> '{t['canonical']}'   ({t['en']})")

if __name__=="__main__":
    main()
